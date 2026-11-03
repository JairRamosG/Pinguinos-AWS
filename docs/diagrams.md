# Diagramas — Arquitectura final

Vista completa del proyecto terminado (estado al final de la Fase 5).
Sin NAT Gateway ni ALB: el acceso es directo a la IP pública, acotado por security groups.

---

## 1. Topología completa

```mermaid
flowchart TB
    subgraph LOCAL["Desarrollo local"]
        W["Navegador / VS Code"]
        G["Repositorio Git"]
    end

    subgraph CI["CI/CD"]
        GH["GitHub Actions<br/>autenticación OIDC"]
    end

    subgraph AWS["Cuenta AWS — us-east-1"]

        subgraph NET["VPC 10.0.0.0/16"]
            subgraph PUB["Subredes públicas"]
                SRV["Servicio ECS Fargate<br/>serve · Streamlit :8501<br/>IP pública"]
                TRN["Tarea efímera<br/>train · Batch / RunTask"]
            end
            GW["S3 Gateway Endpoint<br/>(gratis)"]
        end

        subgraph BOX["EC2 de desarrollo (persistente)"]
            EC2["t3.medium<br/>Docker + JupyterLab"]
            EBS[("EBS 30 GB<br/>notebooks + entorno")]
            MLF["MLflow local<br/>SQLite"]
        end

        subgraph DATA["Almacenamiento"]
            S3D[("S3 datos<br/>raw/ · processed/")]
            S3M[("S3 modelos<br/>model.pkl + manifest.json")]
            ECR["ECR<br/>train · serve · jupyter"]
        end

        subgraph OPS["Operación"]
            CW["CloudWatch<br/>logs + alarmas"]
            BUD["AWS Budgets"]
            PS["Parameter Store<br/>configuración"]
        end

        IAM["IAM<br/>instance profile · task roles<br/>execution roles · rol OIDC"]
    end

    U["Usuario final"]

    W -->|"SSM Session Manager"| EC2
    EC2 --- EBS
    EC2 --> MLF
    EC2 -->|"lee datos"| S3D
    EC2 -->|"pull imagen jupyter"| ECR

    G --> GH
    GH -->|"push imágenes :SHA"| ECR
    GH -->|"cdk deploy"| AWS

    ECR -->|"pull"| TRN
    ECR -->|"pull"| SRV

    S3D -->|"raw/"| TRN
    TRN -->|"escribe artefacto"| S3M
    S3M -->|"carga al arrancar"| SRV

    U -->|":8501, solo tu IP"| SRV

    TRN --> GW
    SRV --> GW
    GW --> S3D

    TRN --> CW
    SRV --> CW
    EC2 --> CW

    IAM -.->|"permisos"| EC2
    IAM -.->|"permisos"| TRN
    IAM -.->|"permisos"| SRV
    BUD -.->|"alerta de gasto"| CW
```

---

## 2. Flujo de entrenamiento

```mermaid
sequenceDiagram
    participant Op as Operador
    participant GH as GitHub
    participant ECR as ECR
    participant T as Tarea train
    participant D as S3 datos
    participant M as S3 modelos
    participant F as MLflow
    participant L as CloudWatch

    Op->>GH: git push (código nuevo)
    GH->>ECR: build + push train:SHA
    Op->>T: run-task (imagen :SHA, --folds 5)
    T->>ECR: pull imagen
    T->>D: GET raw/penguins.csv
    T->>T: valida esquema
    T->>T: build_pipeline() + validación cruzada
    T->>F: registra params + métricas por fold
    T->>M: PUT models/ts/model.pkl
    T->>M: PUT models/ts/manifest.json
    T->>L: métricas y exit code
    T-->>Op: exit 0
    Op->>Op: verifica f1_macro >= umbral
```

---

## 3. Flujo de inferencia

```mermaid
sequenceDiagram
    participant U as Usuario
    participant S as Fargate serve
    participant M as S3 modelos
    participant L as CloudWatch

    S->>M: GET model.pkl + manifest.json (una vez, al arrancar)
    S->>L: "modelo ts cargado"
    U->>S: GET http://<public-ip>:8501 desde tu IP
    S-->>U: UI Streamlit sobre WebSocket
    U->>S: envía medidas del pingüino
    S-->>U: especie + probabilidades
    S->>L: {"event":"prediction","model":"ts"}
```

---

## 4. Flujo de despliegue

```mermaid
sequenceDiagram
    participant Dev as Desarrollador
    participant GH as GitHub
    participant CI as Actions
    participant ECR as ECR
    participant CDK as CDK
    participant AWS as CloudFormation

    Dev->>GH: pull request
    GH->>CI: dispara pipeline
    CI->>CI: pytest
    CI->>CDK: cdk synth + cdk diff
    CI-->>Dev: diff publicado en el PR
    Dev->>GH: merge a main
    GH->>CI: dispara deploy
    CI->>ECR: build + push :SHA
    CI->>CDK: cdk deploy
    CDK->>AWS: actualiza stack
    AWS-->>CI: stack actualizado
```

---

## 5. Qué existe en cada fase

| Componente | Fase 1 | Fase 2 | Fase 3 | Fase 4 | Fase 5 |
|---|---|---|---|---|---|
| S3 datos + modelos | ✅ | ✅ | ✅ | ✅ | ✅ |
| ECR + imágenes | — | ✅ | ✅ | ✅ | ✅ |
| EC2 dev + EBS + SSM | — | — | ✅ | ✅ | ✅ |
| Tarea de entrenamiento | — | manual | ✅ job | ✅ | ✅ |
| Fargate serve | — | — | ✅ IP pública | ✅ | ✅ |
| CloudWatch + alarmas | — | básico | ✅ | ✅ | ✅ |
| Budgets + tags | ✅ | ✅ | ✅ | ✅ | ✅ |
| CI/CD con OIDC | — | — | — | — | ✅ |
| MLflow | — | — | ✅ | ✅ | ✅ |

---

## 6. Costo del estado final

| Recurso | Estado en reposo | Costo aproximado |
|---|---|---|
| S3 (datos + modelos) | siempre | < $1/mes |
| ECR (3 imágenes) | siempre | < $1/mes |
| EBS 30 GB | siempre | ~$2.40/mes |
| EC2 dev | apagada de noche | ~$10/mes |
| Fargate serve | scale to zero | < $2/mes |
| Public IPv4 (2 direcciones) | siempre, mientras existan las tareas | ~$3.65 c/u · ~$7.30/mes |
| **Total** | | **~$25/mes** |

El IPv4 público reemplazó al NAT y al ALB como costo fijo de red: se paga por dirección
(~$3.65/mes cada una), no por una pieza de infraestructura que el proyecto no usa.
