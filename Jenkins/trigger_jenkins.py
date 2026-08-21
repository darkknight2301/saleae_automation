#!/usr/bin/env python3
"""
trigger_jenkins.py

Trigger a parameterized Jenkins build from an already-validated job JSON
file. This script does not validate the JSON — run validate_job.py first.

Usage:
    python3 trigger_jenkins.py jobs/job_001.json

Prints the resulting build number and URL. Does not wait for the build
to complete.
"""

import argparse
import json
import os
import sys
import time

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


def get_crumb(jenkins_url, auth):
    """Fetch a CSRF crumb, if Jenkins has crumb protection enabled."""
    try:
        resp = requests.get(f"{jenkins_url}/crumbIssuer/api/json", auth=auth, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return {data["crumbRequestField"]: data["crumb"]}
    except requests.exceptions.RequestException:
        pass
    return {}


def trigger_build(jenkins_url, auth, job_name, parameters):
    """POST to buildWithParameters and return the queue item URL."""
    headers = get_crumb(jenkins_url, auth)
    build_url = f"{jenkins_url}/job/{job_name}/buildWithParameters"

    resp = requests.post(build_url, auth=auth, headers=headers, data=parameters, timeout=15)
    if resp.status_code == 404:
        print(f"ERROR: Jenkins job '{job_name}' not found (404).")
        sys.exit(1)
    resp.raise_for_status()

    queue_location = resp.headers.get("Location")
    if not queue_location:
        print("ERROR: Jenkins did not return a queue item location.")
        sys.exit(1)
    return queue_location.rstrip("/")


def resolve_build_number(queue_url, auth, retries=15, delay=1.0):
    """Poll the queue item until Jenkins assigns it a build (executable)."""
    for _ in range(retries):
        try:
            resp = requests.get(f"{queue_url}/api/json", auth=auth, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if data.get("cancelled"):
                return None, None
            executable = data.get("executable")
            if executable:
                return executable.get("number"), executable.get("url")
        except requests.exceptions.RequestException:
            pass
        time.sleep(delay)
    return None, None


def main():
    parser = argparse.ArgumentParser(description="Trigger a Jenkins build from a validated job JSON file.")
    parser.add_argument("json_path", help="Path to the validated job JSON file")
    args = parser.parse_args()

    if not os.path.isfile(args.json_path):
        print(f"ERROR: File not found: {args.json_path}")
        sys.exit(1)

    with open(args.json_path) as f:
        job = json.load(f)

    job_name = job["job_name"]
    trigger_id = job["trigger_id"]
    parameters = job["parameters"]

    jenkins_url, user, token = get_jenkins_env()
    auth = (user, token)

    print(f"Triggering Jenkins job '{job_name}' (trigger: {trigger_id}) with parameters: {parameters}")

    try:
        queue_url = trigger_build(jenkins_url, auth, job_name, parameters)
    except requests.exceptions.RequestException as exc:
        print(f"ERROR: Failed to trigger Jenkins job: {exc}")
        sys.exit(1)

    print(f"Build queued: {queue_url}")

    build_number, build_url = resolve_build_number(queue_url, auth)
    if build_number is not None:
        print(f"Build number: {build_number}")
        print(f"Build URL: {build_url}")
    else:
        print("Build number: pending (Jenkins has not assigned it yet)")
        print(f"Check queue status at: {queue_url}/api/json")


if __name__ == "__main__":
    main()
