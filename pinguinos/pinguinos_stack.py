from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    aws_s3 as s3,
    aws_ecr as ecr,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3 as s3, 
    aws_ssm as ssm
)
from constructs import Construct

STOP_SCHEDULE_UTC = "cron(0 5 * * ? *)" 

class PinguinosStack(Stack):

    def __init__(
            self,
            scope: Construct,
            construct_id: str,
            *,
            vpc: ec2.IVpc | None = None,
            **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Bucket para datos y modelos --- 
        bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name = f"pinguinos-{self.account}",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        CfnOutput(
            self,
            "BucketName",
            value=bucket.bucket_name)
        
        # --- Repositorios para las imágenes --- 
        for image in ("train", "serve", "jupyter"):
            repo = ecr.Repository(
                self,
                f"{image.capitalize()}Repository",
                repository_name = f"pinguinos/{image}",
                image_scan_on_push=True,
                lifecycle_rules=[
                    ecr.LifecycleRule(
                        description = "Expire untege images after 14 days",
                        tag_status = ecr.TagStatus.UNTAGGED,
                        max_image_age = Duration.days(14)
                    )
                ],
                removal_policy=RemovalPolicy.DESTROY,
                empty_on_delete=True
            )
            CfnOutput(
                self, 
                f"{image.capitalize()}RepositoryUri",
                value= repo.repository_uri
                )
            
        # --- VPC de inyección de dependencias para test --- 
        dev_vpc = vpc if vpc is not None else ec2.Vpc.from_lookup(
            self, 
            "DefaultVpc",
            is_default = True
        )

        # --- Grupo de seguridad --- 
        dev_sg = ec2.SecurityGroup(
            self, 
            "DevInstanceSecurityGroup",
            vpc = dev_vpc,
            description = "EC2 dev: sin reglas de entrada, acceso solo con SSM",
            allow_all_outbound = True
        )

        # --- Roles de InstancePrile para la instancia EC2 hacie el bucket --- 
        dev_role = iam.Role(
            self,
            "DevInstanceRole",
            assumed_by = iam.ServicePrincipal("ec2.amazonaws.com"),
            description = "Instance Profile de la EC2 de desarrollo (SSM + S3 por prefijo)",
            managed_policies = [
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagesInstanceCore"
                )
            ]
        )

        dev_role.add_to_policy(
            iam.PolicyStatement(
                sid = "ListBucket",
                actions=["s3:ListBucket"],
                resources = [bucket.bucket_arn]
            )
        )

        dev_role.add_to_policy(
            iam.PolicyStatement(
                sid = "ReadRawAndModels",
                actions = ["s3:GetObject"],
                resources = [
                    bucket.arn_for_objects("data/raw/*"),
                    bucket.arn_for_objects("models/*")
                ]
            )
        )

        dev_role.add_to_policy(
            iam.PolicyStatement(
                sid = "WriteModels",
                actions = ["s3:PutObject"],
                resources = [bucket.arn_for_objects("models/*")]
            )
        )

        # --- Instancia de desarrollo ---
        dev_instance = ec2.Instance(
            self,
            "DevInstance",
            vpc=dev_vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType("t3.micro"),
            role = dev_role,
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            security_group=dev_sg,
            associate_public_ip_address=True,
            http_tokens=ec2.HttpTokens.REQUIRED,
            http_put_response_hop_limit=1,
            propagate_tags_to_volume_on_creation=True,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        30,
                        volume_type=ec2.EbsDeviceVolumeType.GP3,
                        encrypted=True,
                        delete_on_termination=True,
                    ),
                )
            ],
        )

        # --- Auto-stop nocturno ---
        stop_role = iam.Role(
            self,
            "DevStopAutomationRole",
            assumed_by = iam.ServicePrincipal("ssm.amazonaws.com"),
            description = "Permite a SSM Automation detener la EC2 de desarrollo",
        )

        stop_role.add_to_policy(
            iam.PolicyStatement(
                sid = "DescribeInstances",
                actions = ["ec2:DescribeInstances"],
                resources = ["*"],
            )
        )

        stop_role.add_to_policy(
            iam.PolicyStatement(
                sid="StopDevInstance",
                actions=["ec2:StopInstances"],
                resources=[
                    self.format_arn(
                        service="ec2", resource="instance", resource_name="*"
                    )
                ],
            )
        )

        ssm.CfnAssociation(
            self,
            "DevNightlyStop",
            name = "AWS-StopEC2Instance",
            association_name = "pinguinos-dev-nightly-stop",
            apply_only_at_cron_interval= True,
            schedule_expression = STOP_SCHEDULE_UTC,
            automation_target_parameter_name = "InstanceId",
            targets = [
                {
                    "key"   : "InstanceIds",
                    "values": [dev_instance.instance_id] 
                }
            ],
            parameters = {
                "AutomationAssumeRole": [stop_role.role_arn]
            }
        )
        CfnOutput(
            self,
            "DevInstanceId",
            value = dev_instance.instance_id)

        Tags.of(self).add("project", "pinguinos")
        Tags.of(self).add("env", "dev")
        Tags.of(self).add("owner", "jair")