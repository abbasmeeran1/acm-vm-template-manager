# ACM VM Template Manager 🚀

Centralized VM template management for OpenShift Virtualization using ACM.

## Features
- Single YAML source of truth
- Push templates to all clusters
- Eliminates template drift

## Requirements
- oc CLI
- kubectx
- Python 3

## Usage
```bash
cp templates.example.yaml templates.yaml
python3 acm-push.py