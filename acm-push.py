#!/usr/bin/env python3
"""
ACM VM Template Manager
======================
Push VM templates to multiple OpenShift clusters via ACM.

Usage:
  python3 acm-push.py
  python3 acm-push.py templates.yaml
"""

import subprocess, json, sys, os

try:
    import yaml
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "pyyaml", "-q"])
    import yaml


# ── helpers ──────────────────────────────────────────────────

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def ok(msg):  print(f"  ✓  {msg}")
def err(msg): print(f"  ✗  {msg}")
def info(msg):print(f"  →  {msg}")

def verify_login():
    r = run(["oc", "whoami"])
    if r.returncode != 0:
        err("Not logged in. Run: oc login")
        sys.exit(1)
    ok(f"Logged in as: {r.stdout.strip()}")

def verify_context(expected="acm-hub"):
    r = run(["kubectx", "--current"])
    ctx = r.stdout.strip() if r.returncode == 0 else "unknown"
    ok(f"Context: {ctx}")
    if expected not in ctx:
        ans = input(f"\n  WARNING: expected '{expected}'. Continue? (y/N): ").strip().lower()
        if ans != "y":
            info("Aborted. Switch context and retry.")
            sys.exit(0)


# ── template builder ─────────────────────────────────────────

def build(t, d):
    g = lambda key: t.get(key, d.get(key))

    cloudinit = (
        "#cloud-config\n"
        "hostname: ${HOSTNAME}\n"
        "fqdn: ${HOSTNAME}.${DOMAIN}\n"
        "prefer_fqdn_over_hostname: true\n"
        "users:\n"
        "  - name: ${CLOUD_USER}\n"
        "    sudo: ALL=(ALL) NOPASSWD:ALL\n"
        "    groups: wheel\n"
        "    lock_passwd: false\n"
        "    plain_text_passwd: ${CLOUD_USER_PASSWORD}\n"
        "ssh_pwauth: true\n"
        "runcmd:\n"
        "  - sleep 5\n"
        "  - IF=$(ls /sys/class/net | grep -v lo | head -1)\n"
        "  - nmcli con add type ethernet con-name $IF ifname $IF ipv4.method manual "
        "ipv4.addresses ${IP_ADDRESS}/${NETMASK} ipv4.gateway ${GATEWAY} "
        "ipv4.dns \"${DNS_SERVER_1} ${DNS_SERVER_2}\" ipv4.dns-search \"${DOMAIN}\" "
        "connection.autoconnect yes\n"
        "  - nmcli con up $IF\n"
    )

    return {
        "complianceType": "musthave",
        "objectDefinition": {
            "apiVersion": "template.openshift.io/v1",
            "kind": "Template",
            "metadata": {
                "name": t["name"],
                "namespace": "openshift",
                "annotations": {
                    "openshift.io/display-name": t.get("display_name", t["name"]),
                    "defaults.template.kubevirt.io/cpu-cores": str(t["cpu_cores"]),
                    "defaults.template.kubevirt.io/cpu-sockets": str(t["cpu_sockets"]),
                    "defaults.template.kubevirt.io/memory": t["memory"],
                },
                "labels": {
                    "template.kubevirt.io/type": "vm",
                    f"flavor.template.kubevirt.io/{t['flavor']}": "true",
                },
            },
            "objects": [{
                "apiVersion": "kubevirt.io/v1",
                "kind": "VirtualMachine",
                "metadata": {"name": "${NAME}"},
                "spec": {
                    "runStrategy": "Halted",
                    "dataVolumeTemplates": [{
                        "apiVersion": "cdi.kubevirt.io/v1beta1",
                        "kind": "DataVolume",
                        "metadata": {"name": "${NAME}-rootdisk"},
                        "spec": {
                            "sourceRef": {
                                "kind": "DataSource",
                                "name": "${DATA_SOURCE_NAME}",
                                "namespace": "${DATA_SOURCE_NAMESPACE}",
                            },
                            "storage": {
                                "accessModes": [g("access_mode")],
                                "resources": {"requests": {"storage": t["disk_size"]}},
                                "storageClassName": g("storage_class"),
                            },
                        },
                    }],
                    "template": {
                        "spec": {
                            "domain": {
                                "cpu": {
                                    "cores": t["cpu_cores"],
                                    "sockets": t["cpu_sockets"],
                                },
                                "memory": {"guest": t["memory"]},
                                "devices": {
                                    "disks": [
                                        {"bootOrder": 1, "disk": {"bus": "sata"}, "name": "rootdisk"},
                                        {"disk": {"bus": "virtio"}, "name": "cloudinitdisk"},
                                    ],
                                    "interfaces": [{"bridge": {}, "name": "net-0"}],
                                },
                            },
                            "networks": [{"multus": {"networkName": "${NETWORK_NAME}"}, "name": "net-0"}],
                            "volumes": [
                                {"dataVolume": {"name": "${NAME}-rootdisk"}, "name": "rootdisk"},
                                {"cloudInitNoCloud": {"userData": cloudinit}, "name": "cloudinitdisk"},
                            ],
                        },
                    },
                },
            }],
            "parameters": [
                {"name": "NAME", "required": True},
                {"name": "HOSTNAME", "required": True},
                {"name": "IP_ADDRESS", "required": True},
                {"name": "NETMASK", "value": "24"},
                {"name": "GATEWAY", "required": True},
                {"name": "DNS_SERVER_1"},
                {"name": "DNS_SERVER_2"},
                {"name": "DOMAIN"},
                {"name": "NETWORK_NAME", "value": g("network")},
                {"name": "DATA_SOURCE_NAME", "value": g("data_source")},
                {"name": "DATA_SOURCE_NAMESPACE", "value": g("data_source_ns")},
                {"name": "CLOUD_USER", "value": d.get("cloud_user", "cloud-user")},
                {"name": "CLOUD_USER_PASSWORD"},  # no default!
            ],
        },
    }


# ── push ─────────────────────────────────────────────────────

def push(policy_name, policy_ns, object_templates):
    policy = {
        "spec": {
            "policy-templates": [{
                "objectDefinition": {
                    "apiVersion": "policy.open-cluster-management.io/v1",
                    "kind": "ConfigurationPolicy",
                    "metadata": {"name": "vm-template-policy"},
                    "spec": {
                        "remediationAction": "enforce",
                        "object-templates": object_templates,
                    },
                }
            }]
        }
    }

    return run([
        "oc", "patch", "policy", policy_name,
        "-n", policy_ns, "--type=merge", "-p", json.dumps(policy)
    ])


# ── main ─────────────────────────────────────────────────────

def main():
    cfg_file = sys.argv[1] if len(sys.argv) > 1 else "templates.yaml"

    if not os.path.exists(cfg_file):
        err(f"Config not found: {cfg_file}")
        sys.exit(1)

    with open(cfg_file) as f:
        cfg = yaml.safe_load(f)

    verify_login()
    verify_context()

    object_templates = [
        build(t, cfg.get("defaults", {}))
        for t in cfg["templates"]
    ]

    r = push(cfg["policy"]["name"], cfg["policy"]["namespace"], object_templates)

    if r.returncode == 0:
        ok("Policy updated successfully")
    else:
        err(r.stderr)


if __name__ == "__main__":
    main()