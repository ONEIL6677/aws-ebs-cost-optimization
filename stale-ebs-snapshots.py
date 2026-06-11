import boto3
import botocore.exceptions


def lambda_handler(event, context):
    ec2 = boto3.client('ec2')

    # ─── Step 1: Get ALL EBS snapshots owned by this account (with pagination) ───
    snapshots = []
    paginator = ec2.get_paginator('describe_snapshots')
    for page in paginator.paginate(OwnerIds=['self']):
        snapshots.extend(page['Snapshots'])

    print(f"Total snapshots found: {len(snapshots)}")

    # ─── Step 2: Get ALL active EC2 instance IDs (with pagination) ───────────────
    active_instance_ids = set()
    instances_paginator = ec2.get_paginator('describe_instances')
    for page in instances_paginator.paginate(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
    ):
        for reservation in page['Reservations']:
            for instance in reservation['Instances']:
                active_instance_ids.add(instance['InstanceId'])

    print(f"Active running instances found: {len(active_instance_ids)}")

    # ─── Step 3: Iterate through snapshots and delete stale ones ─────────────────
    deleted_count = 0
    skipped_count = 0

    for snapshot in snapshots:
        snapshot_id = snapshot['SnapshotId']
        volume_id = snapshot.get('VolumeId')

        # Delete snapshot if it has no associated volume
        if not volume_id:
            _delete_snapshot(ec2, snapshot_id, "not attached to any volume")
            deleted_count += 1
            continue

        # Check if the associated volume still exists
        try:
            volume_response = ec2.describe_volumes(VolumeIds=[volume_id])
            volume = volume_response['Volumes'][0]
            attachments = volume.get('Attachments', [])

            if not attachments:
                # Volume exists but is not attached to any instance — safe to delete
                _delete_snapshot(ec2, snapshot_id, f"its volume ({volume_id}) is not attached to any instance")
                deleted_count += 1
            else:
                # Volume is attached — check if it belongs to a running instance
                attached_instance_id = attachments[0].get('InstanceId')
                if attached_instance_id not in active_instance_ids:
                    _delete_snapshot(
                        ec2, snapshot_id,
                        f"its volume ({volume_id}) is attached to a stopped/terminated instance ({attached_instance_id})"
                    )
                    deleted_count += 1
                else:
                    print(f"Skipping snapshot {snapshot_id} — volume {volume_id} is attached to running instance {attached_instance_id}.")
                    skipped_count += 1

        except botocore.exceptions.ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'InvalidVolume.NotFound':
                # Volume has been deleted — snapshot is now orphaned, safe to remove
                _delete_snapshot(ec2, snapshot_id, f"its associated volume ({volume_id}) no longer exists")
                deleted_count += 1
            else:
                # Unexpected error — log it and skip this snapshot
                print(f"WARNING: Unexpected error checking volume {volume_id} for snapshot {snapshot_id}: {e}")
                skipped_count += 1

    print(f"Summary — Deleted: {deleted_count} snapshot(s) | Skipped: {skipped_count} snapshot(s).")

    return {
        'statusCode': 200,
        'deleted_snapshots': deleted_count,
        'skipped_snapshots': skipped_count
    }


def _delete_snapshot(ec2, snapshot_id, reason):
    """Helper function to delete a snapshot with error handling and logging."""
    try:
        ec2.delete_snapshot(SnapshotId=snapshot_id)
        print(f"Deleted snapshot {snapshot_id} — Reason: {reason}.")
    except botocore.exceptions.ClientError as e:
        print(f"ERROR: Failed to delete snapshot {snapshot_id}: {e}")