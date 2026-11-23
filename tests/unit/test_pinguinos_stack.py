import json

import aws_cdk as core
import aws_cdk.assertions as assertions
from aws_cdk import aws_ec2 as ec2

from pinguinos.pinguinos_stack import PinguinosStack


def _template() -> assertions.Template:
    app = core.App()

    network = core.Stack(app, "network")
    vpc = ec2.Vpc(
        network,
        "TestVpc",
        max_azs=2,
        nat_gateways=0,
        subnet_configuration=[
            ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC)
        ],
    )

    stack = PinguinosStack(app, "pinguinos", vpc = vpc)
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


def _instancia(template: assertions.Template) -> dict:
    instances = template.find_resources("AWS::EC2::Instance")
    assert len(instances) == 1, f"Se esperaba una EC2, hay {len(instances)}"
    return next(iter(instances.values()))

def _rol_para(template: assertions.Template, servicio: str) -> tuple[str, dict]:
    for logical_id, role in template.find_resources("AWS::IAM::Role").items():
        trust = json.dumps(role["Properties"]["AssumeRolePolicyDocument"])
        if servicio in trust:
            return logical_id, role
    raise AssertionError(f"No hay rol asumible por {servicio}")


def _statements_del_rol(template: assertions.Template, servicio: str) -> list[dict]:
    """Junta los statements inline y los de las políticas adjuntas.

    `Role.add_to_policy()` no escribe `Policies` dentro del rol: crea un
    `AWS::IAM::Policy` aparte con `Roles: [{Ref: <rol>}]`. Buscar solo en
    `Properties.Policies` del rol deja fuera justamente esos permisos.
    """
    logical_id, role = _rol_para(template, servicio)
    statements: list[dict] = []
    for policy in role["Properties"].get("Policies", []):
        statements.extend(policy["PolicyDocument"]["Statement"])
    for policy in template.find_resources("AWS::IAM::Policy").values():
        attached = policy["Properties"].get("Roles", [])
        if {"Ref": logical_id} in attached:
            statements.extend(policy["Properties"]["PolicyDocument"]["Statement"])
    return statements


def test_hay_una_ec2_t3_micro():
    template = _template()
    template.resource_count_is("AWS::EC2::Instance", 1)
    assert _instancia(template)["Properties"]["InstanceType"] == "t3.micro"


def test_ec2_sin_keypair():
    template = _template()
    assert "KeyName" not in _instancia(template)["Properties"]


def test_ec2_exige_imdsv2():
    template = _template()
    metadata = _instancia(template)["Properties"]["MetadataOptions"]
    assert metadata["HttpTokens"] == "required"
    assert metadata["HttpPutResponseHopLimit"] == 2


def test_disco_raiz_30_gb_gp3_cifrado_y_se_borra_con_la_instancia():
    template = _template()
    mapping = _instancia(template)["Properties"]["BlockDeviceMappings"][0]
    assert mapping["DeviceName"] == "/dev/xvda"
    ebs = mapping["Ebs"]
    assert ebs["VolumeSize"] == 30
    assert ebs["VolumeType"] == "gp3"
    assert ebs["Encrypted"] is True
    assert ebs["DeleteOnTermination"] is True


def test_security_group_sin_reglas_de_entrada():
    template = _template()
    template.resource_count_is("AWS::EC2::SecurityGroupIngress", 0)
    sgs = template.find_resources("AWS::EC2::SecurityGroup")
    assert len(sgs) == 1
    assert "SecurityGroupIngress" not in next(iter(sgs.values()))["Properties"]


def test_rol_de_instancia_con_ssm():
    template = _template()
    _, role = _rol_para(template, "ec2.amazonaws.com")
    assert "AmazonSSMManagedInstanceCore" in json.dumps(role["Properties"])


def test_rol_s3_acotado_por_prefijo():
    template = _template()
    policies = json.dumps(_statements_del_rol(template, "ec2.amazonaws.com"))
    assert "s3:GetObject" in policies
    assert "s3:PutObject" in policies
    assert "data/raw/*" in policies
    assert "models/*" in policies
    assert '"Resource": "*"' not in policies


def test_la_instancia_tiene_instance_profile():
    template = _template()
    template.resource_count_is("AWS::IAM::InstanceProfile", 1)
    assert "IamInstanceProfile" in _instancia(template)["Properties"]


def test_auto_stop_nocturno():
    template = _template()
    associations = template.find_resources("AWS::SSM::Association")
    assert len(associations) == 1
    props = next(iter(associations.values()))["Properties"]
    assert props["Name"] == "AWS-StopEC2Instance"
    assert props["ScheduleExpression"] == "cron(0 5 * * ? *)"
    assert props["ApplyOnlyAtCronInterval"] is True
    assert props["AutomationTargetParameterName"] == "InstanceId"
    target = props["Targets"][0]
    assert target["Key"] == "ParameterValues"
    assert len(target["Values"]) == 1
    assert "DevInstance" in target["Values"][0]["Ref"]
    assert "AutomationAssumeRole" in props["Parameters"]
    assert "InstanceId" not in props["Parameters"]


def test_el_rol_de_parada_solo_detiene_instancias():
    template = _template()
    policies = json.dumps(_statements_del_rol(template, "ssm.amazonaws.com"))
    assert "ec2:StopInstances" in policies
    assert "instance/*" in policies


def test_la_instancia_lleva_los_tags_del_proyecto():
    template = _template()
    tags = {t["Key"]: t["Value"] for t in _instancia(template)["Properties"]["Tags"]}
    assert tags["project"] == "pinguinos"
    assert tags["env"] == "dev"
    assert tags["owner"] == "jair"


def test_exporta_el_id_de_la_instancia():
    template = _template()
    assert "DevInstanceId" in json.dumps(template.find_outputs("*"))

def test_rol_s3_acotado_por_prefijo():
    template = _template()
    statements = _statements_del_rol(template, "ec2.amazonaws.com")
    s3 = [s for s in statements if "s3:" in json.dumps(s.get("Action", ""))]
    policies = json.dumps(s3)
    assert "s3:GetObject" in policies
    assert "s3:PutObject" in policies
    assert "data/raw/*" in policies
    assert "models/*" in policies
    assert '"Resource": "*"' not in policies

def test_rol_puede_descargar_la_imagen_de_jupyter():
    template = _template()
    statements = _statements_del_rol(template, "ec2.amazonaws.com")
    pull = next(s for s in statements if s.get("Sid") == "PullJupyterImage")
    assert set(pull["Action"]) == {
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
    }
    assert "repository/pinguinos/jupyter" in json.dumps(pull["Resource"])