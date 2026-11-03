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