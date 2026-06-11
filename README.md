# AWS EBS Snapshot Cleanup Lambda Function

An AWS Lambda function that automatically identifies and deletes **stale EBS snapshots**
to reduce unnecessary storage costs keeping only snapshots tied to active,
running EC2 instances.

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
- [Deletion Logic](#deletion-logic)
- [Prerequisites](#prerequisites)
- [Deployment](#deployment)
- [IAM Permissions](#iam-permissions)
- [Scheduling with EventBridge](#scheduling-with-eventbridge)
- [Expected Output](#expected-output)
- [Troubleshooting](#troubleshooting)
- [Security Notes](#security-notes)

---

## Overview

EBS snapshots are incremental backups of EC2 volumes. Over time, snapshots can
accumulate from deleted volumes, stopped instances, or old infrastructure silently
adding to your AWS bill. This Lambda function automates the cleanup process by
scanning all snapshots in your account and safely deleting the ones that are no
longer needed.

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   EventBridge (Scheduled Trigger)                               │
│         │                                                       │
│         ▼                                                       │
│   ┌─────────────────┐                                          │
│   │  Lambda Function │                                          │
│   └────────┬────────┘                                          │
│            │                                                    │
│            ├──► describe_snapshots()  ──► All account snapshots │
│            │                                                    │
│            ├──► describe_instances()  ──► Running instance IDs  │
│            │                                                    │
│            └──► For each snapshot:                              │
│                  ├── No volume?          ──► ✅ DELETE           │
│                  ├── Volume not found?   ──► ✅ DELETE           │
│                  ├── Volume unattached?  ──► ✅ DELETE           │
│                  ├── Attached to stopped instance? ──► ✅ DELETE │
│                  └── Attached to running instance? ──► ⏭ SKIP   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Deletion Logic

The function evaluates each snapshot against the following rules:

| Condition | Action | Reason |
|-----------|--------|--------|
| Snapshot has no associated volume | ✅ Delete | Orphaned snapshot — volume never existed or was removed |
| Associated volume no longer exists | ✅ Delete | Volume was deleted — snapshot is now orphaned |
| Volume exists but is not attached to any instance | ✅ Delete | Detached volume with no active use |
| Volume is attached to a **stopped** or terminated instance | ✅ Delete | Instance is not running — snapshot is stale |
| Volume is attached to a **running** instance | ⏭ Skip | Snapshot is still in active use — keep it |

---

## Prerequisites

Before deploying this function, ensure you have:

- An **AWS account** with permissions to create Lambda functions and IAM roles
- **AWS CLI** installed and configured (`aws configure`)
- **Python 3.8+** (Lambda runtime)
- The `boto3` library (pre-installed in AWS Lambda environments)

---

## Deployment

### Option A — Deploy via AWS Console

1. Log in to the **AWS Management Console**
2. Navigate to **Lambda** → **Create Function**
3. Select **Author from scratch**
4. Configure the function:
   - **Function name:** `ebs-snapshot-cleanup`
   - **Runtime:** Python 3.11 (or 3.8+)
   - **Architecture:** x86_64
5. Click **Create Function**
6. In the **Code** tab, paste the contents of `stale-ebs-snapshots.py`
7. Click **Deploy**
8. Set the **timeout** to at least **5 minutes** under Configuration → General Configuration
   (large accounts with many snapshots may take longer)

---

### Option B — Deploy via AWS CLI

**Step 1 — Package the function:**

```bash
zip ebs-cleanup.zip stale-ebs-snapshots.py
```

**Step 2 — Create the Lambda function:**

```bash
aws lambda create-function \
  --function-name ebs-snapshot-cleanup \
  --runtime python3.11 \
  --handler stale-ebs-snapshots.lambda_handler \
  --role arn:aws:iam::YOUR_ACCOUNT_ID:role/ebs-cleanup-role \
  --zip-file fileb://ebs-cleanup.zip \
  --timeout 300 \
  --memory-size 128 \
  --region us-east-1
```

**Step 3 — Update the function (after changes):**

```bash
zip ebs-cleanup.zip stale-ebs-snapshots.py

aws lambda update-function-code \
  --function-name ebs-snapshot-cleanup \
  --zip-file fileb://ebs-cleanup.zip
```

---

## IAM Permissions

The Lambda execution role must have the following permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EBSSnapshotCleanup",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeSnapshots",
        "ec2:DeleteSnapshot",
        "ec2:DescribeVolumes",
        "ec2:DescribeInstances"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    }
  ]
}
```

### Creating the IAM Role via CLI

```bash
# Create the role
aws iam create-role \
  --role-name ebs-cleanup-role \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": { "Service": "lambda.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }]
  }'

# Attach the basic Lambda execution policy
aws iam attach-role-policy \
  --role-name ebs-cleanup-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# Create and attach the custom EC2 policy
aws iam put-role-policy \
  --role-name ebs-cleanup-role \
  --policy-name EBSSnapshotCleanupPolicy \
  --policy-document file://iam-policy.json
```

---

## Scheduling with EventBridge

To run the cleanup automatically on a schedule, create an EventBridge rule:

### Via AWS Console

1. Go to **EventBridge** → **Rules** → **Create Rule**
2. Set the **Rule name:** `ebs-cleanup-schedule`
3. Select **Schedule** as the rule type
4. Set the schedule expression:
   - Run weekly: `rate(7 days)`
   - Run monthly: `cron(0 2 1 * ? *)` *(1st of every month at 2:00 AM UTC)*
5. Set the **Target** to your Lambda function `ebs-snapshot-cleanup`
6. Click **Create**

### Via AWS CLI

```bash
# Create the EventBridge rule (runs every Sunday at 2:00 AM UTC)
aws events put-rule \
  --name ebs-cleanup-schedule \
  --schedule-expression "cron(0 2 ? * SUN *)" \
  --state ENABLED

# Grant EventBridge permission to invoke the Lambda function
aws lambda add-permission \
  --function-name ebs-snapshot-cleanup \
  --statement-id EventBridgeInvoke \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn arn:aws:events:us-east-1:YOUR_ACCOUNT_ID:rule/ebs-cleanup-schedule

# Add the Lambda function as the rule target
aws events put-targets \
  --rule ebs-cleanup-schedule \
  --targets "Id=1,Arn=arn:aws:lambda:us-east-1:YOUR_ACCOUNT_ID:function:ebs-snapshot-cleanup"
```

---

## Expected Output

### CloudWatch Logs — Successful Run

```
Total snapshots found: 47
Active running instances found: 12
Deleted snapshot snap-0abc123 — Reason: not attached to any volume.
Deleted snapshot snap-0def456 — Reason: its associated volume (vol-0abc123) no longer exists.
Deleted snapshot snap-0ghi789 — Reason: its volume (vol-0def456) is not attached to any instance.
Deleted snapshot snap-0jkl012 — Reason: its volume (vol-0ghi789) is attached to stopped instance (i-0abc123).
Skipping snapshot snap-0mno345 — volume vol-0jkl012 is attached to running instance i-0def456.
Summary — Deleted: 4 snapshot(s) | Skipped: 43 snapshot(s).
```

### Lambda Return Value

```json
{
  "statusCode": 200,
  "deleted_snapshots": 4,
  "skipped_snapshots": 43
}
```
## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `AccessDenied` on `describe_snapshots` | Missing IAM permission | Add `ec2:DescribeSnapshots` to the Lambda role |
| `AccessDenied` on `delete_snapshot` | Missing IAM permission | Add `ec2:DeleteSnapshot` to the Lambda role |
| Function times out | Too many snapshots to process | Increase Lambda timeout to 10–15 minutes under Configuration → General |
| No logs appearing | CloudWatch log permissions missing | Add `logs:CreateLogGroup`, `logs:PutLogEvents` to the role |
| Function runs but deletes nothing | All snapshots are attached to running instances | Expected behaviour — no stale snapshots found |
| `InvalidSnapshot.NotFound` error | Snapshot was deleted between describe and delete calls | Harmless race condition — already handled in the corrected code |

---

## Security Notes

- The IAM policy uses `"Resource": "*"` for EC2 actions consider scoping to a
  specific region or account if operating in a multi-account environment
- **Always test in a non-production account first** before running in production
- Enable **AWS CloudTrail** to audit all `DeleteSnapshot` API calls made by this function
- Consider adding a **dry-run mode** by commenting out `ec2.delete_snapshot()` and
  reviewing the logs before enabling actual deletion
- Never grant this Lambda function broader permissions than the four EC2 actions listed above

---

## Author

**ONEIL KIMBI**
Version: v1.0.0
