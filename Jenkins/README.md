# Jenkins Trigger Utility

A lightweight, script-based utility for triggering parameterized Jenkins jobs
in response to an external trigger. No database, API server, scheduler,
queue service, or background process — just three small scripts you run by
hand or wire into whatever external system delivers the trigger.

## Workflow

```
Jenkins Job Name
      ↓
jenkins_template.py            → generates jobs/job_XXX.json (blank parameters)
      ↓
User fills JSON                → job_name, trigger_id, parameters
      ↓
Send "Job Scheduled" email     → (see email_templates.md, copy/paste manually)
      ↓
Wait for external trigger
      ↓
validate_job.py + trigger      → checks the JSON is valid and the trigger matches
      ↓
trigger_jenkins.py             → calls the Jenkins REST API
      ↓
Jenkins Build
      ↓
Send "Job Triggered" email     → (see email_templates.md, copy/paste manually)
```

Each JSON file in `jobs/` represents one independent job. Files are not run
sequentially — when a trigger arrives, only the job JSON matching that
trigger is validated and triggered. Different jobs can be triggered and run
at the same time.

## Setup

```bash
pip install -r requirements.txt
```

## Configuration

Set these environment variables (never hardcode credentials):

```bash
export JENKINS_URL="https://jenkins.example.com"
export JENKINS_USER="your-username"
export JENKINS_TOKEN="your-api-token"
```

`JENKINS_TOKEN` should be a Jenkins API token (User → Configure → API Token),
not your login password.

## 1. Generate a job template

Queries Jenkins for the job's parameter definitions and writes a blank JSON
template. Does not trigger anything.

```bash
python3 jenkins_template.py SSD_Test --output jobs/job_001.json
```

Produces:

```json
{
    "job_name": "SSD_Test",
    "trigger_id": "",
    "parameters": {
        "IP": "",
        "PRODUCT": "",
        "USER": ""
    }
}
```

## 2. Fill in the JSON

Manually fill in `trigger_id` (the ID that the external trigger will send)
and each parameter value:

```json
{
    "job_name": "SSD_Test",
    "trigger_id": "TRIGGER_001",
    "parameters": {
        "IP": "10.10.10.10",
        "PRODUCT": "SSD_A",
        "USER": "user1"
    }
}
```

At this point, send the "Job Scheduled" email using the template in
`email_templates.md`.

## 3. Validate a job against a received trigger

When a trigger arrives, validate the corresponding job JSON against it. This
checks the JSON structure, required values, that the trigger ID matches, and
(if Jenkins is reachable) that the job and its parameters still exist as
expected on Jenkins. It does not trigger a build.

```bash
python3 validate_job.py jobs/job_001.json --trigger TRIGGER_001
```

Exit code `0` means valid; any non-zero exit code means invalid. Check the
exit code (`echo $?`) or the printed `Result: VALID` / `Result: INVALID`
line before proceeding.

## 4. Trigger the Jenkins build

Only run this after `validate_job.py` has passed. This script does not
re-validate the JSON — it trusts that step 3 already happened.

```bash
python3 trigger_jenkins.py jobs/job_001.json
```

Prints the queued build number and Jenkins build URL, then exits — it does
not wait for the build to finish.

At this point, send the "Job Triggered" email using the template in
`email_templates.md`.

## Example end-to-end workflow

```bash
# One-time setup
export JENKINS_URL="https://jenkins.example.com"
export JENKINS_USER="alice"
export JENKINS_TOKEN="xxxxxxxxxxxxxxxx"

# Generate templates for each job you expect to run
python3 jenkins_template.py SSD_Test --output jobs/job_001.json
python3 jenkins_template.py HDD_Test --output jobs/job_002.json

# Fill in jobs/job_001.json and jobs/job_002.json by hand,
# each with its own trigger_id (e.g. TRIGGER_A, TRIGGER_B)

# ... send "Job Scheduled" emails ...

# Later, TRIGGER_B arrives from the external system:
python3 validate_job.py jobs/job_002.json --trigger TRIGGER_B
if [ $? -eq 0 ]; then
    python3 trigger_jenkins.py jobs/job_002.json
fi

# ... send "Job Triggered" email ...
```

## Project structure

```
jenkins_trigger/
├── jenkins_template.py   # Job name -> blank JSON template (queries Jenkins, no trigger)
├── validate_job.py       # Validates a filled JSON against a trigger (no trigger)
├── trigger_jenkins.py    # Triggers Jenkins from an already-validated JSON
├── email_templates.md    # Copy/paste "Job Scheduled" / "Job Triggered" templates
├── jobs/                 # Generated / filled-in job JSON files
├── README.md
└── requirements.txt
```
