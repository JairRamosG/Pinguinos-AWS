import json

import aws_cdk as core
import aws_cdk.assertions as assertions

from pinguinos.pinguinos_stack import PinguinosStack


def _template() -> assertions.Template:
    app = core.App()
    stack = PinguinosStack(app, "pinguinos")
    return assertions.Template.from_stack(stack)


def test_solo_hay_un_bucket():
    template = _template()
    template.resource_count_is("AWS::S3::Bucket", 1)


def test_bucket_privado_y_versionado():
    template = _template()
    template.has_resource_properties("AWS::S3::Bucket", {
        "VersioningConfiguration": {"Status": "Enabled"},
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        },
    })


def test_bucket_se_borra_con_el_stack():
    template = _template()
    buckets = template.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 1
    bucket = next(iter(buckets.values()))
    assert bucket["DeletionPolicy"] == "Delete"
    assert bucket["UpdateReplacePolicy"] == "Delete"

def test_hay_tres_repositorios_ecr():
    template = _template()
    template.resource_count_is("AWS::ECR::Repository", 3)


def test_repositorios_tienen_los_nombres_esperados():
    template = _template()
    names = {
        repo["Properties"]["RepositoryName"]
        for repo in template.find_resources("AWS::ECR::Repository").values()
    }
    assert names == {"pinguinos/train", "pinguinos/serve", "pinguinos/jupyter"}


def test_ecr_escanea_al_push():
    template = _template()
    for repo in template.find_resources("AWS::ECR::Repository").values():
        props = repo["Properties"]
        assert props["ImageScanningConfiguration"] == {"ScanOnPush": True}


def test_lifecycle_expira_untagged_a_14_dias():
    template = _template()
    for repo in template.find_resources("AWS::ECR::Repository").values():
        text = repo["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"]
        policy = json.loads(text)
        rule = policy["rules"][0]
        assert rule["selection"]["tagStatus"] == "untagged"
        assert rule["selection"]["countType"] == "sinceImagePushed"
        assert rule["selection"]["countNumber"] == 14
        assert rule["selection"]["countUnit"] == "days"


def test_los_repositorios_se_borran_con_el_stack():
    template = _template()
    for repo in template.find_resources("AWS::ECR::Repository").values():
        assert repo["DeletionPolicy"] == "Delete"
        assert repo["UpdateReplacePolicy"] == "Delete"