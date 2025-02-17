# Quiz Game Notifier

This project deploys an AWS Lambda function that sends Telegram notifications about Quiz Please game schedules. The function retrieves game data from a DynamoDB table and sends a formatted message using a Telegram bot. Deployment is managed via Terraform.

## Table of Contents
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [How It Works](#how-it-works)
- [Environment Variables](#environment-variables)
- [Local Development](#local-development)
- [Clean Up](#clean-up)

## Project Structure

```plaintext
├── src
│   ├── main.py                # Lambda function code
│   ├── requirements.txt       # Python dependency definitions
│   └── (other source files or folders)
└── terraform
    ├── main.tf                # Terraform configuration
    ├── variables.tf           # Input variables
    ├── backend.hcl            # Backend configuration (not committed; see below)
    └── (other Terraform files)
├── README.md
```

- **src**: Contains the Lambda function source code and the dependency definition.
- **terraform**: Contains the Terraform configuration for deploying AWS resources.

## Prerequisites

- DynamoDB table with game data. Check the [Quiz Please Game Registration](https://github.com/selfadjoint/quiz-please-reg) and [Quiz Please Poll Creator](https://github.com/selfadjoint/quiz-please-poll) projects for the required DynamoDB table setup.
- [AWS CLI](https://aws.amazon.com/cli/)
- [Terraform](https://www.terraform.io/)
- [Python 3.11+](https://www.python.org/)
- [pip](https://pip.pypa.io/en/stable/)

## Setup

### 1. Clone the Repository

Clone this repository and navigate to the project root:

```bash
git clone https://github.com//selfadjoint/quiz-please-notifier.git
cd quiz-please-notifier
```
### 2. Install Python Dependencies
The dependencies are not committed to the repository. To install them into the src folder, run:
```bash
pip install --upgrade --target ./src -r src/requirements.txt
```
This command installs all required Python packages into the src directory so that they are included in the Lambda deployment package.

### 3. Configure the Terraform Backend and Variables
Terraform uses an S3 backend for state storage. Since sensitive information should not be committed to the repository, create a separate backend configuration file.

Create a file named `backend.hcl` inside the `terraform` folder with content similar to:

```hcl
bucket       = "your-tf-state-bucket"                  # Replace with your S3 bucket name
key          = "your-resource-name/terraform.tfstate"  # Adjust as needed
region       = "us-east-1"                             # Your AWS region
profile      = "your_aws_profile"                      # The AWS CLI profile to use
encrypt      = true
use_lockfile = true
```
**Create a `terraform.tfvars` file with the necessary variables. Example**:

```hcl
aws_profile        = "your_aws_profile"
bot_token          = "your_bot_token"
group_id           = "your_group_id"
dynamodb_reg_table = "your_dynamodb_table"
```

### 4. Initialize Terraform
Change to the terraform directory and initialize Terraform using the backend configuration:
```bash
cd terraform
terraform init -backend-config=backend.hcl
```
This command sets up the backend and downloads required providers.

### 5. Review and Apply the Terraform Configuration
First, run a plan to see the changes that Terraform will apply:
```bash
terraform plan
```

If everything looks correct, deploy the resources with:
```bash
terraform apply
```
Confirm the apply action when prompted.

## How It Works
- **Packaging the Lambda**:
Terraform uses the archive_file data source to package the entire src folder into a `lambda.zip` file. This archive includes your main.py and all installed dependencies.

- **Lambda Configuration**:
The Lambda function is configured with a handler (`main.lambda_handler`), a runtime (`python3.11`), and environment variables (e.g., `BOT_TOKEN`, `GROUP_ID`, `DYNAMODB_REG_TABLE`). These variables are used by your Python code to send notifications and query DynamoDB.

- **IAM & CloudWatch**:
Terraform creates an IAM role with the necessary permissions, attaches policies for Lambda execution and DynamoDB access, and sets up a CloudWatch Events rule to trigger the Lambda function on schedule.

## Environment Variables
The Lambda function expects the following environment variables, which are set in the Terraform configuration:

- **BOT_TOKEN**: Your Telegram Bot API token.
- **GROUP_ID**: The target Telegram group or channel ID.
- **DYNAMODB_REG_TABLE**: The name of the DynamoDB table holding game registration data.

Ensure these are provided via your Terraform variable files or however you manage Terraform variables.

## Local Development
For local testing and development, you can run the Lambda function code by setting the required environment variables. For example:
```bash
export BOT_TOKEN="your_bot_token"
export GROUP_ID="your_group_id"
export DYNAMODB_REG_TABLE="your_dynamodb_table"
python src/main.py
```

You can use an IDE like PyCharm to develop and debug your code.

## Clean Up
To remove all resources created by Terraform, run:
```bash
terraform destroy
```
This will tear down the deployed AWS resources.
