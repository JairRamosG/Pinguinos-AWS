from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    aws_s3 as s3,
    aws_ecr as ecr
)
from constructs import Construct

class PinguinosStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

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

        Tags.of(self).add("project", "pinguinos")
        Tags.of(self).add("env", "dev")
        Tags.of(self).add("owner", "jair")