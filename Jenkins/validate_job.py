#!/usr/bin/env python3
"""
validate_job.py

Validate a filled-in job JSON file against a received trigger ID, and
(where practical) against Jenkins itself.

Usage:
    python3 validate_job.py jobs/job_001.json --trigger TRIGGER_001

Exit code:
    0     = valid
    non-0 = invalid

This script never triggers a Jenkins build.
"""

import argparse
import json
import os
import sys

import requests


def get_jenkins_env():
    """Read Jenkins connection settings from environment variables.

    Returns (None, None, None) if any are missing, so Jenkins-side checks
    can be skipped gracefully instead of failing local validation outright.
    """
    url = os.environ.get("JENKINS_URL")
    user = os.environ.get("JENKINS_USER")
    token = os.environ.get("JENKINS_TOKEN")

    if not (url and user and token):
        return None, None, None
    return url.rstrip("/"), user, token


def get_job_parameter_names(jenkins_url, auth, job_name):
    api_url = f"{jenkins_url}/job/{job_name}/api/json?tree=actions[parameterDefinitions[name]]"
    resp = requests.get(api_url, auth=auth, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    param_names = []
    for action in data.get("actions", []):
        for pdef in (action or {}).get("parameterDefinitions", []) or []:
            if pdef.get("name"):
                param_names.append(pdef["name"])
    return param_names


def main():
    parser = argparse.ArgumentParser(description="Validate a job JSON file against a received trigger.")
    parser.add_argument("json_path", help="Path to the filled-in job JSON file")
    parser.add_argument("--trigger", required=True, help="Trigger ID that was received")
    args = parser.parse_args()

    errors = []

    # --- Load and parse JSON ---
    if not os.path.isfile(args.json_path):
        print(f"ERROR: File not found: {args.json_path}")
        sys.exit(1)

    try:
        with open(args.json_path) as f:
            job = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"ERROR: Invalid JSON in {args.json_path}: {exc}")
        sys.exit(1)

    # --- Required top-level keys ---
    for key in ("job_name", "trigger_id", "parameters"):
        if key not in job:
            errors.append(f"Missing required field: '{key}'")

    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        print("Result: INVALID")
        sys.exit(1)

    job_name = job["job_name"]
    trigger_id = job["trigger_id"]
    parameters = job["parameters"]

    # --- Required values ---
    if not job_name:
        errors.append("'job_name' is empty")
    if not trigger_id:
        errors.append("'trigger_id' is empty")
    if not isinstance(parameters, dict):
        errors.append("'parameters' must be a JSON object")
    else:
        for name, value in parameters.items():
            if value == "" or value is None:
                errors.append(f"Parameter '{name}' has no value")

    # --- Trigger match ---
    if trigger_id and trigger_id != args.trigger:
        errors.append(f"Trigger mismatch: JSON has '{trigger_id}', received '{args.trigger}'")

    # --- Jenkins job/parameter consistency (best effort) ---
    jenkins_url, user, token = get_jenkins_env()
    if jenkins_url and job_name:
        try:
            jenkins_params = set(get_job_parameter_names(jenkins_url, (user, token), job_name))
            json_params = set(parameters.keys()) if isinstance(parameters, dict) else set()

            missing_in_json = jenkins_params - json_params
            unknown_in_json = json_params - jenkins_params

            if missing_in_json:
                errors.append(f"Parameters required by Jenkins job but missing from JSON: {', '.join(sorted(missing_in_json))}")
            if unknown_in_json:
                errors.append(f"Parameters in JSON not recognized by Jenkins job: {', '.join(sorted(unknown_in_json))}")
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                errors.append(f"Jenkins job '{job_name}' not found")
            else:
                print(f"WARNING: Could not verify against Jenkins (HTTP error): {exc}")
        except requests.exceptions.RequestException as exc:
            print(f"WARNING: Could not reach Jenkins to verify job/parameter consistency: {exc}")
    else:
        print("WARNING: JENKINS_URL/JENKINS_USER/JENKINS_TOKEN not set — skipping Jenkins-side consistency check.")

    # --- Result ---
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        print("Result: INVALID")
        sys.exit(1)

    print(f"Job name: {job_name}")
    print(f"Trigger ID: {trigger_id}")
    print(f"Parameters: {parameters}")
    print("Result: VALID")
    sys.exit(0)


if __name__ == "__main__":
    main()
