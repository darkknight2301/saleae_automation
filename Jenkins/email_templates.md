# Email Templates

Copy/paste templates only. No email sending is implemented by this utility.

## Job Scheduled

```
Subject: [Jenkins] Job Scheduled - <JOB_NAME>

Job Name: <JOB_NAME>
Trigger ID: <TRIGGER_ID>
Parameters:
  <PARAM_NAME>: <PARAM_VALUE>
  <PARAM_NAME>: <PARAM_VALUE>
Scheduled Time: <YYYY-MM-DD HH:MM:SS>
Status: WAITING FOR TRIGGER
```

## Job Triggered

```
Subject: [Jenkins] Job Triggered - <JOB_NAME>

Job Name: <JOB_NAME>
Trigger ID: <TRIGGER_ID>
Parameters:
  <PARAM_NAME>: <PARAM_VALUE>
  <PARAM_NAME>: <PARAM_VALUE>
Triggered Time: <YYYY-MM-DD HH:MM:SS>
Build Number: <BUILD_NUMBER>
Jenkins URL: <BUILD_URL>
Status: TRIGGERED
```
