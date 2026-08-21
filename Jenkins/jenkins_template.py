#!/usr/bin/env python3
"""
jenkins_template.py

Query Jenkins for a job's parameter definitions and generate a blank JSON
template that a user can fill in.

Usage:
    python3 jenkins_template.py SSD_Test --output jobs/job_001.json

This script never triggers a Jenkins build.
"""

import argparse
import json
import os
import sys

import requests


def get_jenkins_env():
    """Read Jenkins connection settings from environment variables."""
    url = os.environ.get("JENKINS_URL")
    user = os.environ.get("JENKINS_USER")
    token = os.environ.get("JENKINS_TOKEN")

    missing = [name for name, val in (
        ("JENKINS_URL", url),
        ("JENKINS_USER", user),
        ("JENKINS_TOKEN", token),
    ) if not val]

    if missing:
        print(f"ERROR: Missing required environment variable(s): {', '.join(missing)}")
        sys.exit(1)

    return url.rstrip("/"), user, token


def get_job_parameter_names(jenkins_url, auth, job_name):
    """Return the list of parameter names defined on a Jenkins job."""
    api_url = f"{jenkins_url}/job/{job_name}/api/json?tree=actions[parameterDefinitions[name]]"
    resp = requests.get(api_url, auth=auth, timeout=15)
    if resp.status_code == 404:
        print(f"ERROR: Jenkins job '{job_name}' not found (404).")
        sys.exit(1)
    resp.raise_for_status()
    data = resp.json()

    param_names = []
    for action in data.get("actions", []):
        for pdef in (action or {}).get("parameterDefinitions", []) or []:
            if pdef.get("name"):
                param_names.append(pdef["name"])
    return param_names


def main():
    parser = argparse.ArgumentParser(description="Generate a JSON job template from a Jenkins job's parameters.")
    parser.add_argument("job_name", help="Name of the Jenkins job")
    parser.add_argument("--output", required=True, help="Path to write the generated JSON template")
    args = parser.parse_args()

    jenkins_url, user, token = get_jenkins_env()

    print(f"Querying Jenkins job '{args.job_name}' at {jenkins_url} ...")
    try:
        param_names = get_job_parameter_names(jenkins_url, (user, token), args.job_name)
    except requests.exceptions.RequestException as exc:
        print(f"ERROR: Failed to query Jenkins: {exc}")
        sys.exit(1)

    template = {
        "job_name": args.job_name,
        "trigger_id": "",
        "parameters": {name: "" for name in param_names},
    }

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(template, f, indent=4)
        f.write("\n")

    print(f"Found {len(param_names)} parameter(s): {', '.join(param_names) if param_names else '(none)'}")
    print(f"Template written to {args.output}")


if __name__ == "__main__":
    main()
