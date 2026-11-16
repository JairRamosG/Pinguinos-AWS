# Cómo funciona este proyecto — apuntes vivos

> **Documento vivo.** Se actualiza al cerrar cada issue.
> Última actualización: 2026-11-15 · Fase actual: 1 (inferencia local completa)

Este documento no reemplaza a `architecture.md` (el *qué*) ni a
`implementation-plan.md` (el *en qué orden*). Es el **modelo mental**: cómo encajan
las piezas y cómo se mueve un cambio desde tu editor hasta la nube.

---

## Cómo usar este documento

- Léelo antes de cada sesión de trabajo. Son 5 minutos y te devuelve el contexto.
- Al cerrar un issue, se actualizan: la tabla de estado (§11), el mapa acumulado (§12) y
  el registro de cambios (§13).
- Si algo de aquí contradice al código, **el código gana** y este documento se corrige.

---

## 1. La regla que ordena todo

**Tu código no corre en la nube. Lo que corre es una imagen.**

Esa frase resuelve el 80 % de la confusión inicial. Tu laptop tiene archivos `.py`; la nube
tiene contenedores ejecutándose. Entre ambos hay dos artefactos intermedios: el commit y la
imagen.

Corolario: **editar un archivo no cambia nada en la nube**. Hace falta un camino completo
hasta el despliegue.

---

## 2. Los cuatro artefactos

| Artefacto | Lo produce | Vive en | Contiene |
|---|---|---|---|
| **Código** | `git commit` | GitHub | La fuente de verdad |
| **Imagen** | `docker build` | ECR | Python + dependencias + tu paquete |
| **Modelo** | `python -m penguins_ml.train` | `artifacts/` → S3 | `model.pkl` + `manifest.json` + `cv.json` |
| **Template** | `cdk synth` | `cdk.out/` → CloudFormation | Qué recursos deben existir |

```mermaid
flowchart LR
    SRC["src/ + configs/<br/>(código fuente)"] -->|"git commit"| GIT[("GitHub")]
    SRC -->|"docker build"| IMG["imagen :SHA"]
    IMG -->|"docker push"| ECR[("ECR")]
    SRC -->|"python -m penguins_ml.train"| ART["artefacto de modelo<br/>model.pkl + manifest"]
    ART -->|"upload"| S3A[("S3 modelos")]
    CDK["pinguinos_stack.py"] -->|"cdk synth"| TPL["template JSON"]
    TPL -->|"cdk deploy"| CFN["CloudFormation"]
    CFN -->|"crea"| INFRA["recursos reales"]
    ECR -->|"pull"| INFRA
```

Ninguno reemplaza a otro:

- El código no corre en la nube. **La imagen corre.**
- La imagen no sabe dónde vive. **El template lo decide.**
- El template no contiene tu lógica. **Solo declara recursos.**
- El **modelo** no se construye: se **produce ejecutando** el entrenamiento. La imagen es
  el código; el artefacto es el resultado. Cambiar el modelo no exige reconstruir la imagen.

---

## 3. Cómo se acomoda el código

```text
pinguinos/
├── src/penguins_ml/          ← el paquete: se instala DENTRO de las imágenes
│   ├── config.py             ← lee y valida la config
│   ├── data.py               ← load_dataframe() (stub) + validate_schema()
│   ├── transforms.py         ← build_pipeline(): compartido por train y serve
│   ├── provenance.py         ← hashes, git, fingerprint y manifest
│   ├── train.py              ← ENTRYPOINT de la imagen train
│   ├── serving.py            ← carga del artefacto y predicción (sin Streamlit)
│   └── serve.py              ← ENTRYPOINT de la imagen serve: UI de Streamlit
├── configs/
│   ├── base.toml             ← qué entrenar
│   ├── local.toml            ← dónde, en tu laptop
│   └── cloud.toml            ← dónde, en AWS
├── Dockerfile.base           ← Python + uv + deps + el paquete
├── docker/
│   ├── train.Dockerfile      ← FROM base + ENTRYPOINT train
│   ├── serve.Dockerfile      ← FROM base + extra serve + Streamlit
│   └── jupyter.Dockerfile    ← FROM base + extras dev/jupyter + JupyterLab
├── scripts/
│   ├── build-images.sh       ← construye base -> train/serve/jupyter
│   └── build-and-push.sh     ← publica en ECR con credenciales temporales
├── docker-compose.yml        ← levanta Jupyter y serve en local
├── .dockerignore             ← qué NO entra al contexto de build
├── pinguinos/                ← CDK: describe la infraestructura
│   └── pinguinos_stack.py
├── notebooks/                ← NUNCA se despliega
└── tests/
```

**La pieza que hace que todo funcione:** `src/penguins_ml/` se instala en **las dos**
imágenes. Por eso el preprocesamiento no puede divergir entre entrenamiento e inferencia:
es literalmente el mismo código.

`train.py` y `serve.py` son dos puertas de entrada al mismo edificio. Uno entrena, el otro
predice, y ambos importan `transforms.py`.

---

## 4. El entrenamiento y su artefacto

El CLI `python -m penguins_ml.train` corre en **cuatro fases**, y cada una falla antes
de la siguiente:

```mermaid
flowchart LR
    C["1. config<br/>TOML + overrides"] --> V["2. datos<br/>read_csv + validate_schema"]
    V --> T["3. entrenamiento<br/>CV + fit final"]
    T --> A["4. artefacto<br/>escritura atómica"]
    C -.->|"exit 2"| X["falla"]
    V -.->|"exit 1"| X
    T -.->|"exit 1"| X
```

| Fase | Qué hace | Si falla |
|---|---|---|
| **1. Config** | `load_config(*paths)` + overrides de CLI | exit **2** (error de uso) |
| **2. Datos** | `read_csv` + `validate_schema` + `folds ≤ clase minoritaria` | exit **1** |
| **3. Entrenamiento** | `cross_validate` para métricas y después `fit` sobre todo el dataset | exit **1** |
| **4. Artefacto** | escritura atómica en `artifacts/<run_id>/` | exit **1** |

La salida es un **directorio**, no un archivo:

```text
artifacts/<run_id>/
├── model.pkl        ← Pipeline completo (preprocesador + estimador)
├── manifest.json    ← procedencia: de dónde salió
└── cv.json          ← métricas por fold
```

### Por qué tres archivos

| Archivo | Responde |
|---|---|
| `model.pkl` | "¿cómo predigo?" |
| `cv.json` | "¿cómo rindió en cada fold?" |
| `manifest.json` | "¿de dónde salió?" |

El manifest es el **contrato de procedencia**:

| Campo | Responde |
|---|---|
| `code.git_sha` / `git_dirty` | qué código lo produjo (y si había cambios sin commitear) |
| `data.etag` / `rows` / `bytes` | sobre qué bytes exactos se entrenó |
| `runtime` | con qué versiones de Python, sklearn y pandas |
| `config` + `config_hash` | con qué receta exacta |
| `config_sources` | qué capas de TOML se cargaron |
| `overrides` | qué se pisó por CLI |
| `cv` + `metrics` | cómo se evaluó |
| `tracking_uri` | reservado para la Fase 4 (MLflow) |

### La escritura es atómica

El artefacto se escribe primero en `artifacts/.tmp-<uuid>/` y recién al final se renombra
a `artifacts/<run_id>/`. Si el proceso muere a mitad, **no queda un artefacto parcial**:
un `.pkl` truncado que parece válido es peor que ninguno.

### Exit codes: el contrato con quien te lanza

| Código | Significado | Quién lo usa |
|---|---|---|
| `0` | Artefacto completo | ECS marca la task exitosa |
| `1` | Falla de ejecución | Reintentar o alertar |
| `2` | Error de config/uso | No reintentar: el input está mal |

En la nube nadie mira la pantalla: el **exit code** es la señal que usa ECS para decidir
si el entrenamiento fue exitoso.

### Corrida real

```bash
python -m penguins_ml.train \
  --config configs/base.toml \
  --config configs/local.toml
```

```text
INFO artefacto: artifacts/2026-11-06T19-43-15Z
INFO manifest: sha256:deb1b8cf...
INFO accuracy: 0.9913 +- 0.0116
INFO f1_macro: 0.9906 +- 0.0121
```

Que `accuracy` y `f1_macro` estén cerca no es casual: el dataset está moderadamente
balanceado. Si divergieran, `f1_macro` sería la señal de que una clase se está perdiendo.

---

## 5. Cómo se construyen las imágenes

No hay un Dockerfile por imagen. Hay **una base y tres derivadas**:

```mermaid
flowchart LR
    BASE["penguins/base:local<br/>Python + uv + deps + paquete"] --> TRAIN["penguins/train:local<br/>ENTRYPOINT del CLI"]
    BASE --> SERVE["penguins/serve:local<br/>+ streamlit"]
    BASE --> JUPY["penguins/jupyter:local<br/>+ jupyterlab"]
```

### Los archivos

| Archivo | Qué define |
|---|---|
| `Dockerfile.base` | Python 3.11 + uv + dependencias + el paquete |
| `docker/train.Dockerfile` | `FROM base` + entrypoint `python -m penguins_ml.train` |
| `docker/serve.Dockerfile` | `FROM base` + extra `serve` + Streamlit |
| `docker/jupyter.Dockerfile` | `FROM base` + extras `dev`/`jupyter` + JupyterLab |
| `.dockerignore` | qué **no** entra al contexto de build |
| `scripts/build-images.sh` | construye las cuatro en orden |
| `scripts/build-and-push.sh` | construye con SHA y publica `train`/`serve`/`jupyter` en ECR |
| `docker-compose.yml` | levanta Jupyter local |

### El orden es obligatorio

```bash
./scripts/build-images.sh          # base -> train / serve / jupyter
```

Las derivadas empiezan con `FROM penguins/base:local`. Si la base no existe, fallan con
`pull access denied`. Y si reconstruís la base, las derivadas **no** se actualizan solas:
son fotos, no referencias vivas.

### Publicar en ECR (`build-and-push.sh`)

`build-and-push.sh` es el camino de publicación. No reconstruye lógica: reutiliza
`build-images.sh` con el SHA corto y empuja las tres imágenes.

```bash
./scripts/build-and-push.sh
```

Usa el perfil SSO `mi-asus` por defecto; `AWS_PROFILE=otro` lo sobreescribe.

1. Exige el árbol de git limpio: el tag debe identificar exactamente un commit.
2. Resuelve la cuenta con `aws sts get-caller-identity` y arma el registro
   `<cuenta>.dkr.ecr.us-east-1.amazonaws.com`.
3. Obtiene un token con `aws ecr get-login-password` y hace `docker login`.
4. Construye `penguins/{train,serve,jupyter}:<SHA>` con `build-images.sh`.
5. Etiqueta y empuja a `pinguinos/{train,serve,jupyter}:<SHA>`.
6. Verifica que el label `org.opencontainers.image.revision` sea el SHA completo.

| Local | ECR |
|---|---|
| `penguins/train:<SHA>` | `pinguinos/train:<SHA>` |
| `penguins/serve:<SHA>` | `pinguinos/serve:<SHA>` |
| `penguins/jupyter:<SHA>` | `pinguinos/jupyter:<SHA>` |

#### Credenciales aisladas

El script crea un `DOCKER_CONFIG` temporal con `mktemp -d`, lo exporta y lo borra con
`trap ... EXIT`. `docker login` y `docker push` usan ese directorio, no
`~/.docker/config.json`. Por eso no depende de `credsStore: desktop`, de `pass` ni de
ningún credential helper local. El token de ECR queda en el directorio temporal (0700)
y desaparece al terminar; si Docker avisa que lo guarda sin cifrar, es esperado.

La infraestructura ECR vive en CDK: tres repositorios `pinguinos/train`,
`pinguinos/serve` y `pinguinos/jupyter`, con escaneo al push, lifecycle para imágenes
untagged a 14 días y `RemovalPolicy.DESTROY` + `empty_on_delete=True`. Los tests de CDK
cubren los tres repos, los nombres, el escaneo, el lifecycle y el borrado.

### Qué entra y qué no

| Entra a la imagen | No entra |
|---|---|
| `src/`, `configs/`, `pyproject.toml`, `uv.lock` | `data/`, `.git`, `.venv`, `artifacts/`, `notebooks/` |

Datos y secretos se **montan** en runtime, nunca se hornean. Un secreto en una capa queda
para siempre, aunque lo borres después: `docker history` lo muestra.

### Procedencia dentro del contenedor

El `.git` no entra a la imagen (lo excluye `.dockerignore`), así que `git rev-parse` no
funciona adentro. El SHA se inyecta en el build:

```bash
docker build --build-arg GIT_SHA="$(git rev-parse HEAD)" ...
```

```dockerfile
ARG GIT_SHA=unknown
ENV PENGUINS_GIT_SHA=${GIT_SHA}
```

Y `provenance.py` lee primero `PENGUINS_GIT_SHA`; si no existe, usa git. Por eso el manifest
generado dentro del contenedor tiene el SHA real y no `unknown`.

### Tamaños medidos

| Imagen | Tamaño |
|---|---|
| `base` | 829 MB |
| `train` | 829 MB |
| `serve` | 1.24 GB |
| `jupyter` | 1.25 GB |

La diferencia entre 1.38 GB y 829 MB fue un `chown -R` después de instalar: en overlayfs
duplica el `.venv` en una capa nueva. Se evita con `COPY --chown` y `USER` **antes** del
`uv sync`.

### Cómo se ejecuta

```bash
# entrenar (data y artifacts montados)
docker run --rm \
  -v "$PWD/data:/app/data:ro" \
  -v "$PWD/artifacts:/app/artifacts" \
  penguins/train:local --config configs/base.toml --config configs/local.toml

# servir
docker run -d --name serve -p 8501:8501 penguins/serve:local

# jupyter
docker compose up jupyter
```

### Las tres reglas

1. Corre como `appuser` (uid 1000), **no root**.
2. **No contiene datos ni credenciales.**
3. En ECR el tag es el SHA corto (`build-and-push.sh`); en desarrollo, `local`.

---

## 6. Cómo se sirve el modelo (inferencia)

El ciclo local se cierra con `serve.py`: carga el artefacto **una vez** y devuelve
predicciones.

```mermaid
flowchart LR
    A["artifacts/<run_id>/"] -->|"streamlit run"| S["serve.py<br/>(Streamlit)"]
    S -->|"predict_proba"| P["especie<br/>+ probabilidades"]
```

### De dónde sale el modelo

Precedencia, de mayor a menor:

1. `--model-uri` (argumento del script)
2. `PENGUINS_MODEL_URI` (variable de entorno, inyectada por compose)
3. el **último** artefacto de `artifacts/`

El artefacto se monta `:ro`: la inferencia lo lee, pero **no puede modificarlo**.

### Cómo se ejecuta

```bash
docker compose up serve          # http://localhost:8501
# o sin contenedor
uv run streamlit run src/penguins_ml/serve.py
```

### Servir desde S3

`PENGUINS_MODEL_URI` también acepta URIs `s3://`:

```bash
PENGUINS_MODEL_URI=s3://pinguinos-792440910424/models/ docker compose up serve
```

- Con barra final (`models/`), lista los prefijos y toma el run más reciente.
- Sin barra, o terminada en `model.pkl`, usa ese run exacto.
- El contenedor monta `~/.aws:ro`. Si el token SSO caduca, `aws sso login` en el host
  y reiniciar el contenedor.
- `serving._materialize_s3` descarga `model.pkl` y `manifest.json` a un directorio
  temporal; la lógica local de carga no cambia.

### Las tres reglas de inferencia

1. **Se carga una vez** (`@st.cache_resource`), no por cada petición.
2. **Nunca reentrena ni escribe** el artefacto.
3. **La UI muestra la versión** (`run_id`, `config_hash`, métricas) leída del
   `manifest.json`: el usuario siempre sabe qué modelo está hablando.

### Separación de lógica

`serve.py` tiene la UI; `serving.py` tiene la carga y la predicción, sin importar
Streamlit. Por eso los tests pueden probar la lógica sin levantar una sesión, y la
UI se prueba con `AppTest`, el harness oficial de Streamlit.

---

## 7. Los tres viajes de un cambio

Saber **cuál** de estos tres flujos estás haciendo es la habilidad operativa central.

### A) Cambias lógica de la aplicación

```mermaid
flowchart LR
    E["editas src/"] --> T["pytest"] --> B["docker build :SHA"]
    B --> P["docker push a ECR"] --> D["cdk deploy<br/>(nuevo tag)"]
    D --> R["la tarea descarga :SHA"]
```

### B) Cambias infraestructura

```mermaid
flowchart LR
    E["editas pinguinos_stack.py"] --> S["cdk synth"] --> DF["cdk diff"] --> D["cdk deploy"]
```

### C) Cambias un notebook

```mermaid
flowchart LR
    E["editas el notebook"] --> G["git push"] --> N["nada se despliega"]
```

El flujo C es el que más se confunde. **Un notebook no requiere deploy.** Es exploración,
no producción.

---

## 8. Cómo manipulas la infraestructura

**No con clics. Con Python.**

```python
# pinguinos/pinguinos_stack.py
bucket = s3.Bucket(
    self, "DataBucket",
    versioned=True,
    removal_policy=RemovalPolicy.DESTROY,
    auto_delete_objects=True,
)
```

Ese archivo **es** el panel de control. Cambias el archivo y CDK calcula la diferencia entre
lo que declaras y lo que existe.

| Comando | Qué hace | Toca AWS |
|---|---|---|
| `cdk synth` | Convierte tu Python en template | No |
| `cdk diff` | Compara template vs realidad | Solo lee |
| `cdk deploy` | Aplica la diferencia | **Sí** |
| `cdk destroy` | Elimina el stack | **Sí** |
| `cdk bootstrap` | Prepara la cuenta y la región (una vez) | **Sí** |

Ciclo diario de infraestructura: `editar → synth → diff → deploy`. El `diff` es tu revisión
antes de tocar la cuenta.

---

## 9. Cómo se siente vivo

"Vivo" no es una sensación. Son **señales verificables**:

| Señal | Cómo la ves |
|---|---|
| El servicio responde | `curl http://<ip>:8501/_stcore/health` → `ok` |
| Alguien usó el modelo | una línea en CloudWatch con `"event":"prediction"` |
| La versión correcta sirve | el log dice `modelo <ts> cargado` |
| El pipeline produjo algo nuevo | el manifest en S3 cambió de timestamp |
| Nada se cayó | la alarma está en `OK`, no en `ALARM` |

**El momento en que se siente vivo** no es cuando despliegas: es cuando **rompes algo, lo ves
en los logs, lo arreglas y se recupera**. Ahí entiendes que hay un sistema, no una demo.

---

## 10. Las seis preguntas del especialista

No es saber más servicios. Es poder responder estas seis **sin abrir la consola**:

1. ¿Dónde corre esto exactamente?
2. ¿Qué versión está corriendo ahora?
3. ¿Qué pasa si se cae?
4. ¿Cuánto cuesta este mes?
5. ¿Puedo destruirlo y recrearlo desde cero?
6. ¿Cómo sé que está sano?

Un junior responde 1 y 2 mirando la consola. Un especialista responde las seis con comandos,
y sabe **qué le falta** cuando alguna no tiene respuesta.

---

## 11. Estado del proyecto

Actualizado al: **2026-11-15**

| Issue | Estado | Qué existe ya |
|---|---|---|
| #1 Línea base | ✅ cerrado | `docs/` commiteado · MFA en usuario y root · budget $5/mes · `CDKToolkit` v32 |
| #2 Bucket S3 | ✅ construido y destruido | Bucket privado, versionado, cifrado, `DESTROY` + `auto_delete_objects` · 3 tests · ciclo completo verificado **con un objeto dentro** |
| #3 Paquete | ✅ cerrado | `penguins_ml` instalable · `pyproject.toml` con grupos `dev`/`infra` · `uv.lock` (40 paquetes) · config TOML validada con Pydantic · 8 tests |
| #4 Pipeline + EDA | ✅ cerrado | `ColumnTransformer` + `Pipeline` · `categorical` validada contra `features` · `validate_schema` · EDA con tabla de decisiones · 20 tests |
| #5 Entrenamiento | ✅ cerrado | CLI en 4 fases · CV estratificada + `f1_macro` · `model.pkl` + `manifest.json` + `cv.json` · escritura atómica · exit codes `0/1/2` · 30 tests |
| #6 Imágenes | ✅ cerrado | `Dockerfile.base` + `train`/`serve`/`jupyter` · script de build · compose para Jupyter · no-root · sin datos · procedencia inyectada · 34 tests · 829 MB / 1.24 GB / 1.25 GB |
| #7 Streamlit local | ✅ cerrado | `serving.py` (lógica) + `serve.py` (UI) · servicio `serve` en compose · montaje `:ro` · precedencia `--model-uri` > env > último · 46 tests |
| #8 S3 + sin keys | ✅ cerrado | `storage.py` (local/S3) · `load_dataframe` y fingerprint sobre `s3://` · `train` publica en S3 con `manifest.json` al final · `serve` descarga a temporal y toma el run más reciente · contenedor con `~/.aws:ro` · 13 tests con moto · run real en S3 · 59 tests |
| #9 ECR | ✅ construido | 3 repos `pinguinos/train`, `pinguinos/serve`, `pinguinos/jupyter` · scan on push · lifecycle untagged 14 días · `DESTROY` + `empty_on_delete` · 5 tests de CDK · `build-and-push.sh` con tag SHA y credenciales temporales |
| #10 EC2 dev | ⬜ pendiente | — |
| #11 Jupyter | ⬜ pendiente | — |
| #12 MLflow | ⬜ pendiente | — |
| #13 Training job | ⬜ pendiente | — |
| #14 Fargate serve | ⬜ pendiente | — |
| #15 Operación | ⬜ pendiente | — |
| #16 Alarmas y costo | ⬜ pendiente | — |
| #17 CI/CD | ⬜ pendiente | — |
| #18 Drill | ⬜ pendiente | — |

**Dos estados distintos que conviene no confundir:**

- **Construido** = existe el código, los tests y la capacidad de desplegarlo.
- **Desplegado** = existe ahora mismo en la cuenta.

Este proyecto trabaja en modo construir-y-destruir: lo construido persiste en git, lo desplegado
es efímero.

**Lo que existe hoy en AWS** (además del bootstrap):

```mermaid
flowchart LR
    subgraph AWS["Cuenta AWS · us-east-1"]
        TK["CDKToolkit<br/>bucket de assets · 5 roles · SSM v32"]
        BUD["Budget Pinguinos-Mensual<br/>$5/mes · alerta 80%"]
    end
    NADA["Sin recursos de aplicación desplegados<br/>(el bucket S3 se destruyó tras verificarlo)"]
```

---

## 12. Mapa acumulado

Este diagrama crece con el proyecto. Cada cierre de issue agrega su bloque.

```mermaid
flowchart TB
    subgraph HOY["Construido"]
        BOOT["CDKToolkit<br/>(desplegado)"]
        S3["bucket S3 privado<br/>versionado · cifrado · destruible<br/>(verificado y destruido)"]
        PKG["penguins_ml<br/>paquete + config + pipeline"]
        TRN["CLI de entrenamiento<br/>local o S3 · model.pkl + manifest"]
        IMG["imágenes<br/>base · train · serve · jupyter"]
        UI["inferencia local o desde S3<br/>Streamlit + compose"]
        ECR["ECR<br/>pinguinos/train · serve · jupyter<br/>scan + lifecycle 14d"]
    end
    subgraph DESPUES["Lo que viene"]
        EC2["EC2 dev<br/>Jupyter + MLflow"]
        JOB["training job"]
        SRV["Fargate serve"]
        OBS["CloudWatch + alarmas"]
        CI["CI/CD OIDC"]
    end
    BOOT --> S3 --> PKG --> TRN --> IMG --> UI --> ECR --> EC2 --> JOB --> SRV --> OBS --> CI
```

---

## 13. Registro de cambios

| Fecha | Cambio |
|---|---|
| 2026-11-03 | Creación del documento. Issue #1 en curso: bootstrap completado, budget creado, MFA activo. |
| 2026-11-03 | Issue #1 cerrado. Issue #2 construido: bucket S3 privado, versionado, cifrado, con `DESTROY` + `auto_delete_objects`; 3 tests; ciclo deploy/destroy verificado con un objeto dentro. Se agrega la distinción construido ≠ desplegado. |
| 2026-11-03 | Issue #3 cerrado: paquete `penguins_ml`, `pyproject` con grupos, `uv.lock`, config TOML validada y 8 tests. El EDA se integra como **paso 0 del issue #4**, no como issue aparte, para no fragmentar el trabajo. |
| 2026-11-04 | Issue #4 cerrado: `ColumnTransformer` + `Pipeline`, `categorical` validada contra `features`, `validate_schema` y 20 tests. El EDA queda documentado en `notebooks/01_eda.ipynb`. |
| 2026-11-06 | Issue #5 cerrado: CLI de entrenamiento en 4 fases, CV estratificada con `accuracy` + `f1_macro`, manifest de procedencia, `cv.json`, escritura atómica y exit codes `0/1/2`. Verificado con 30 tests y una corrida real (0.9913 ± 0.0116). |
| 2026-11-07 | Issue #6 cerrado: `Dockerfile.base` + imágenes `train`/`serve`/`jupyter`, `.dockerignore`, script de build y compose para Jupyter. Corre como `appuser`, no contiene datos y la procedencia se inyecta por `--build-arg` (el `git_sha` ya no es `unknown`). Medido: 829 MB / 829 MB / 1.24 GB / 1.25 GB. Se agrega el capítulo §5. |
| 2026-11-10 | Issue #7 cerrado: `serving.py` (lógica de carga y predicción, testeable sin Streamlit) + `serve.py` (UI), servicio `serve` en compose con montaje `:ro`, precedencia `--model-uri` > `PENGUINS_MODEL_URI` > último artefacto. 12 tests nuevos con `AppTest`; suite en 46. Se agrega el capítulo §6: el ciclo local queda completo (train → artefacto → UI). |
| 2026-11-13 | Issue #8 cerrado: `storage.py` abstrae local y S3; `load_dataframe`, `dataset_fingerprint`, `train` y `serve` aceptan `s3://`. El artefacto se publica con `manifest.json` al final y `serve` toma el run más reciente si la URI termina en `/`. Verificado contra AWS real con perfil SSO; 59 tests (13 con moto). El bucket se destruyó tras la verificación. Se agrega §6 "Servir desde S3". |
| 2026-11-15 | Issue #9 construido: 3 repositorios ECR (`pinguinos/train`, `pinguinos/serve`, `pinguinos/jupyter`) con scan on push, lifecycle untagged a 14 días y `DESTROY` + `empty_on_delete`; 5 tests de CDK. `build-and-push.sh` publica las imágenes con tag SHA y aísla las credenciales en un `DOCKER_CONFIG` temporal que se borra al salir. Se agrega §5 "Publicar en ECR". |

---

## 14. Referencias

| Documento | Rol |
|---|---|
| `docs/architecture.md` | El *qué*: componentes, decisiones, costos |
| `docs/implementation-plan.md` | El *en qué orden*: 8 fases con criterios de salida |
| `docs/diagrams.md` | Diagramas de topología y flujos de secuencia |
| `docs/how-it-works.md` | Este documento: modelo mental y apuntes de repaso |
| GitHub Issues #1–#18 | El trabajo concreto, uno por corte vertical |

---

## Conceptos que todavía no necesitas

Deliberadamente fuera hasta que el dolor aparezca:

- NAT Gateway, ALB, subredes privadas.
- EKS / Kubernetes.
- SageMaker Training Jobs y Model Registry.
- Múltiples cuentas y AWS Organizations.
- Service mesh, feature store, GPU.

Cada uno resuelve un problema real. Ninguno es el tuyo todavía.
