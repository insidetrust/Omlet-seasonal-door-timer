#!/usr/bin/env bash
# Deploy coopclock to AWS Lambda + EventBridge Scheduler. Idempotent.
set -euo pipefail

REGION="${AWS_REGION:-eu-west-1}"
FUNCTION="coopclock"
ROLE="coopclock-lambda-role"
SCHED_ROLE="coopclock-scheduler-role"
SECRET="coopclock/omlet-api-key"
SCHEDULE="coopclock-daily"
# Must match your config's location.timezone.
TIMEZONE="${TIMEZONE:-Europe/London}"
RUN_AT="${RUN_AT:-2}"   # local hour of the daily run
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${CONFIG_FILE:-$ROOT/config/coop.yaml}"
BUILD="$ROOT/build"

echo "==> Region: $REGION"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

# ---------------------------------------------------------------- secret
if ! aws secretsmanager describe-secret --secret-id "$SECRET" --region "$REGION" >/dev/null 2>&1; then
  echo "==> Creating secret $SECRET (placeholder)"
  aws secretsmanager create-secret --name "$SECRET" --region "$REGION" \
    --description "Omlet SmartCoop API key for coopclock" \
    --secret-string "REPLACE_ME" >/dev/null
else
  echo "==> Secret $SECRET already exists"
fi
SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "$SECRET" --region "$REGION" --query ARN --output text)"

# ------------------------------------------------------------ lambda role
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  echo "==> Creating role $ROLE"
  aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]
    }' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  echo "==> Waiting for role propagation"
  sleep 12
fi

aws iam put-role-policy --role-name "$ROLE" --policy-name coopclock-secret-read \
  --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"}]
  }"
ROLE_ARN="$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)"

# ---------------------------------------------------------------- package
echo "==> Building deployment package"
rm -rf "$BUILD" && mkdir -p "$BUILD"
pip install --quiet --target "$BUILD" -r "$ROOT/requirements.txt"
cp -r "$ROOT/src/coopclock" "$BUILD/coopclock"
mkdir -p "$BUILD/config" && cp "$CONFIG_FILE" "$BUILD/config/coop.yaml"
find "$BUILD" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
(cd "$BUILD" && zip -qr "$ROOT/coopclock.zip" .)
echo "==> Package: $(du -h "$ROOT/coopclock.zip" | cut -f1)"

# ---------------------------------------------------------------- function
if aws lambda get-function --function-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1; then
  echo "==> Updating function code"
  aws lambda update-function-code --function-name "$FUNCTION" --region "$REGION" \
    --zip-file "fileb://$ROOT/coopclock.zip" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FUNCTION" --region "$REGION" \
    --timeout 900 --memory-size 256 \
    --environment "Variables={OMLET_SECRET_ID=$SECRET,COOPCLOCK_CONFIG=/var/task/config/coop.yaml}" >/dev/null
else
  echo "==> Creating function $FUNCTION"
  aws lambda create-function --function-name "$FUNCTION" --region "$REGION" \
    --runtime python3.12 --handler coopclock.handler.lambda_handler \
    --role "$ROLE_ARN" --timeout 900 --memory-size 256 \
    --zip-file "fileb://$ROOT/coopclock.zip" \
    --environment "Variables={OMLET_SECRET_ID=$SECRET,COOPCLOCK_CONFIG=/var/task/config/coop.yaml}" >/dev/null
fi
aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"
FN_ARN="$(aws lambda get-function --function-name "$FUNCTION" --region "$REGION" --query Configuration.FunctionArn --output text)"

# ------------------------------------------------------------- sched role
if ! aws iam get-role --role-name "$SCHED_ROLE" >/dev/null 2>&1; then
  echo "==> Creating role $SCHED_ROLE"
  aws iam create-role --role-name "$SCHED_ROLE" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{"Effect":"Allow","Principal":{"Service":"scheduler.amazonaws.com"},"Action":"sts:AssumeRole"}]
    }' >/dev/null
  sleep 12
fi
aws iam put-role-policy --role-name "$SCHED_ROLE" --policy-name invoke-coopclock \
  --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"lambda:InvokeFunction\",\"Resource\":\"$FN_ARN\"}]
  }"
SCHED_ROLE_ARN="$(aws iam get-role --role-name "$SCHED_ROLE" --query Role.Arn --output text)"

# ---------------------------------------------------------------- schedule
# Default 02:00 local: after midnight so the date is correct, and outside the
# door's operating window at every latitude we care about. 02:00 also occurs
# exactly once on both DST transition nights, whereas e.g. 01:30 is skipped in
# spring and repeated in autumn.
SCHED_ARGS=(--name "$SCHEDULE" --region "$REGION"
  --schedule-expression "cron(0 $RUN_AT * * ? *)"
  --schedule-expression-timezone "$TIMEZONE"
  --flexible-time-window '{"Mode":"OFF"}'
  --target "{\"Arn\":\"$FN_ARN\",\"RoleArn\":\"$SCHED_ROLE_ARN\",\"Input\":\"{}\"}")

if aws scheduler get-schedule --name "$SCHEDULE" --region "$REGION" >/dev/null 2>&1; then
  echo "==> Updating schedule"
  aws scheduler update-schedule "${SCHED_ARGS[@]}" >/dev/null
else
  echo "==> Creating schedule"
  aws scheduler create-schedule "${SCHED_ARGS[@]}" >/dev/null
fi

echo
echo "==> Deployed."
echo "    Function : $FN_ARN"
echo "    Schedule : ${RUN_AT}:00 $TIMEZONE (DST-aware)"
echo "    Secret   : $SECRET"
echo
echo "Set the API key (never paste it into a chat window):"
echo "  aws secretsmanager put-secret-value --secret-id $SECRET --region $REGION --secret-string 'YOUR_KEY'"
