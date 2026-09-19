> 이 문서는 [English README](README.md)의 한국어판입니다. 최신 내용은 영어판이 기준입니다.

# orrery

**이 서버를 내리면 무엇이 같이 죽습니까?**

지금 이 질문에 답하는 방법은 대개 셋 중 하나입니다. 오래된 위키 문서를 찾아본다. 이 시스템을 잘 아는 선배에게 묻는다. 아니면 그냥 꺼보고 누가 소리치는지 기다린다.

orrery는 그 답을 계산합니다.

```
$ orrery blast host-a1

root: host-a1 (host)
  hop 1: node-a1 (node), db-stock (database)
  hop 2: svc-web (service), svc-inventory (service)
  hop 3: svc-checkout (service)
impacted: 5 / 16
```

서버 한 대를 골랐더니 세 단계 건너 결제 서비스까지 나옵니다. `host-a1`과 `svc-checkout`은 직접 연결된 적이 없습니다. 그 사이에 노드, 데이터베이스, 재고 서비스가 있고, 사람이 머릿속으로 세 단계를 따라가기는 어렵습니다.

---

## 두 번째 명령이 진짜입니다

위 결과는 **구조적으로 닿는 범위**입니다. 다섯 개가 영향권에 있다는 것이지, 다섯 개가 다 죽는다는 뜻은 아닙니다. 실제로 무엇이 죽는지는 복제본이 몇 개인지, 이중화가 있는지에 달려 있습니다.

```
$ orrery simulate host-a1

  host-a1        -> down      passthrough
  node-a1        -> down      passthrough
  db-stock       -> down
  svc-web        -> degraded  replicas=3
  svc-inventory  -> down      replicas=1
  svc-checkout   -> down      hard dep down
```

같은 서버인데 결과가 다릅니다.

- `svc-web`은 **성능 저하**입니다. 복제본이 3개라 하나를 잃어도 서비스는 삽니다.
- `svc-inventory`는 **죽습니다**. 복제본이 1개뿐입니다.
- `svc-checkout`은 재고 서비스에 강하게 의존하므로 **연쇄로 죽습니다**.

새벽에 알아야 할 건 "다섯 개가 영향받는다"가 아니라 **"체크아웃이 멈춘다, 원인은 재고 서비스의 복제본이 하나뿐이기 때문"**입니다. 첫 번째 명령이 범위를 주고, 두 번째 명령이 결과를 줍니다.

---

## 5분 만에 직접 해보기

```bash
git clone <this repo> && cd orrery
uv sync
uv run orrery ingest fixtures/demo-world.yaml
uv run orrery blast site-a
uv run orrery simulate db-stock
```

`fixtures/demo-world.yaml`은 합성 데이터입니다. 서버 3대, 클러스터 1개, 서비스 4개, DB 2개로 된 작은 가상 회사이고 실제 어느 회사와도 무관합니다. 열어 보면 형식이 바로 보입니다.

```yaml
entities:
  - {id: host-a1, kind: host, name: "a1"}
  - {id: svc-checkout, kind: service, name: "checkout", attrs: {replicas: 2}}
  - {id: db-orders, kind: database, attrs: {engine: postgres, replica: true}}

relations:
  - {src: node-a1, dst: host-a1, kind: RUNS_ON}
  - {src: svc-checkout, dst: db-orders, kind: DEPENDS_ON}
```

**개체**와 **관계** 둘뿐입니다. 관계는 다섯 종류입니다.

| 관계 | 읽는 법 | 예 |
|---|---|---|
| `RUNS_ON` | A가 B 위에서 돈다 | 서비스가 노드 위에서 |
| `HOSTED_IN` | A가 B 안에 놓여 있다 | 서버가 IDC 안에 |
| `MEMBER_OF` | A가 B의 구성원이다 | 노드가 클러스터의 |
| `DEPENDS_ON` | A가 B를 필요로 한다 | 서비스가 DB를 |
| `CONNECTS_TO` | A가 B와 통신한다 | 네트워크 경로 |

앞의 넷이 영향 전파 경로입니다. B가 죽으면 B를 가리키는 A들이 영향을 받습니다.

---

## 지도는 두 층으로 되어 있습니다

의존성 지도를 만드는 간선에는 두 종류가 있습니다. 나오는 곳이 다르고, 드는 품이 다르고,
질문의 다른 절반에 답합니다. 이 둘을 섞는 것이 여기서 할 수 있는 가장 비싼 실수입니다.
첫 번째 층만 있는 지도는 **완성된 것처럼 보이면서 틀린 답을 주기** 때문입니다.

```mermaid
flowchart TB
  subgraph call["호출 층 — 누가 누구를 부르나"]
    direction LR
    web["웹 프론트"] -->|DEPENDS_ON| checkout["체크아웃"]
    checkout -->|DEPENDS_ON| inventory["재고"]
    inventory -->|DEPENDS_ON| db[("재고 DB")]
  end

  subgraph infra["인프라 층 — 무엇이 무엇 위에 있나"]
    direction LR
    node["노드"] -->|RUNS_ON| host["서버"]
    host -->|HOSTED_IN| site["IDC"]
    host -->|CONNECTS_TO| seg["네트워크 대역"]
    node -->|MEMBER_OF| cluster["클러스터"]
  end

  call -.->|"RUNS_ON: 서비스가 노드 위에서 돈다"| infra
```

| | 인프라 층 | 호출 층 |
|---|---|---|
| 묻는 것 | 무엇이 무엇 위에 있나 | 누가 누구를 부르나 |
| 간선 | `RUNS_ON` · `HOSTED_IN` · `MEMBER_OF` · `CONNECTS_TO` | `DEPENDS_ON` |
| 어디서 오나 | 인벤토리 — CMDB, 클라우드 API, 쿠버네티스 | **어느 인벤토리에도 없음.** 코드가 하는 일이라 자산 목록에 안 적힘 |
| 드는 품 | 소스마다 커넥터 하나 | 제일 어려운 부분 |
| 없으면 | 지도가 아예 없음 | "체크아웃이 멈춘다" 대신 "서버가 죽었다" |

**두 층은 정체성으로 이어집니다.** 호출 층의 서비스가 인프라 층의 노드 **위에서 돕니다**.
이 이음매를 틀리면 하나가 둘로 쪼개지고, 쪼개진 양쪽이 자신 있게 틀린 답을 줍니다. 개체
해소가 있는 이유가 정확히 이 이음매이고, 그래서 **추측하지 않습니다**.

### 사람들이 빠뜨리는 층: 클러스터가 무엇 위에 서 있나

가상화는 아무도 의도하지 않은 채로 이중화를 가짜로 만듭니다.

```
서비스 → 노드 → VM → 물리 서버 → 랙 → IDC
                └──── 여기가 빠진다 ────┘
```

**쿠버네티스는 자기가 무엇 위에 서 있는지 모릅니다.** 노드 세 개는 실패할 자리 세 개처럼
보이는데, 그 셋이 한 물리 서버 위의 가상머신 셋이면 자리는 하나입니다. 클러스터 안에서는
누구도 이걸 알려줄 수 없고, 그래서 **이 질문을 할 수 있는 곳은 지도뿐입니다.**

`vm`이 `host`와 별개의 종류인 이유가 정확히 이것이고, `orrery check`가 잡아냅니다.

```
redundancy on one machine (1)
  svc-api    3 places to run, all of them on host-phys-1 — losing it loses all of them
```

**IDC를 공유하는 건 보고하지 않습니다.** 한 데이터센터 안에 다 있는 건 결함이 아니라 사실이고,
모든 서비스에 소견을 달면 사람들이 리포트를 안 보게 됩니다.

### 호출 층은 계측 없이도 만들 수 있습니다

인벤토리는 어디서 도는지는 알아도 무엇을 부르는지는 모릅니다. 알아내는 방법이 셋인데,
셋이 똑같이 쓸 수 있는 건 아닙니다.

| 방법 | 앱을 건드리나 | 대가 |
|---|---|---|
| **분산 트레이싱** — OpenTelemetry, Jaeger, Zipkin | **그렇다.** 서비스마다 라이브러리 | 모든 서비스에 계측이 가능하고 모든 구간이 맥락을 전파해야 성립. 한 곳만 못 해도 거기서부터 그래프가 끊긴다. 네이티브·레거시 코드에는 대개 불가 |
| **eBPF** — Pixie, SkyWalking Rover, Hubble | 아니다 | 쿠버네티스 모양. 커널 요건. 노드마다 에이전트 |
| **연결 관측** — 플로우 로그, 방화벽 로그, 소켓 테이블 | **아니다** | 거칠다. `호스트 A → 호스트 B:9000`까지이고 어느 엔드포인트인지는 모름. 관측 지점을 안 지나는 트래픽은 안 보임 |

상용 도구는 대부분 첫 번째를 전제합니다. 그래서 처음부터 쿠버네티스로 지은 곳에서는 훌륭하고,
십오 년에 걸쳐 자란 곳에서는 거의 안 됩니다.

세 번째는 앱을 하나도 안 건드리고 두 사실을 조인해 호출 층을 만듭니다.

```
"호스트 A가 호스트 B의 9000번으로 트래픽을 보낸다"   (플로우 또는 방화벽 로그)
"B의 9000번은 재고 서비스다"                          (프로세스 목록)
──────────────────────────────────────────────
A의 서비스가 재고 서비스를 DEPENDS_ON 한다
```

거친 걸로 충분합니다. blast radius가 묻는 건 **무엇이 깨지나**이지 어느 API가 깨지나가
아니고, 그 질문에는 리스닝 포트가 곧 서비스입니다.

---

## 내 인프라를 넣으려면

지도가 없으면 아무 답도 못 합니다. 그래서 데이터를 넣는 게 전부인데, 여기가 실제로 어려운 부분입니다.

**커넥터를 직접 씁니다.** orrery는 커넥터 인터페이스만 정의하고, 실제 시스템에 붙는 코드는 각자의 리포에 둡니다. 회사마다 CMDB도 다르고 모니터링도 다르기 때문입니다.

```python
from orrery.connectors.base import Connector, Discovery
from orrery.schema import Entity, Relation, EntityKind, RelationKind

class MyCmdbConnector:
    name = "mycmdb"

    def discover(self) -> Discovery:
        d = Discovery()
        for row in my_cmdb_api.list_servers():
            d.entities.append(Entity(
                id=f"host-{row['id']}", kind=EntityKind.host, name=row["hostname"],
                attrs={"ip": row["ip"], "env": row["env"]},
            ))
            d.relations.append(Relation(
                src=f"host-{row['id']}", dst=f"site-{row['idc']}",
                kind=RelationKind.HOSTED_IN,
            ))
        return d
```

`discover()` 하나만 구현하면 됩니다. 여러 커넥터의 결과는 합쳐집니다.

**이름이 시스템마다 다른 문제.** 같은 서비스를 CMDB는 `inventory`, 모니터링은 `inventory-prod`라고 부릅니다. `orrery resolve`가 후보를 찾아 주지만 **자동으로 합치지 않습니다.**

```
$ orrery resolve fixtures/demo-world.yaml
candidate: svc-inventory (inventory) | svc-inventory-prod (inventory-prod)
```

사람이 확인한 것만 합칩니다. 잘못 합친 지도는 없는 지도보다 나쁩니다. 틀린 답을 자신 있게 주기 때문입니다.

---

## 이건 무엇이 아닙니까

- **모니터링이 아닙니다.** 지금 뭐가 아픈지는 기존 모니터링이 알려 줍니다. orrery는 *아직 일어나지 않은 일*을 계산합니다.
- **서비스 맵이 아닙니다.** APM의 서비스 맵은 관측된 트래픽을 그립니다. 트래픽이 흐르지 않는 야간 배치, 장애 조치 경로, 아직 부하가 없는 신규 서비스는 안 보입니다. orrery는 선언된 구조를 씁니다.
- **CMDB가 아닙니다.** 인벤토리는 기존 CMDB가 갖고 있습니다. orrery는 그 위에서 질문에 답하는 계산 계층입니다. 저장소를 하나 더 만들지 마세요. 정본이 둘이면 반드시 어긋납니다.
- **두 번째 프로덕션이 아닙니다.** 모든 것을 실제로 돌리지 않습니다. 대부분은 객체와 행동 모델이고, 필요한 구역만 자세히 계산합니다.

---

## 쓰게 되는 순간들

**변경 전.** 이 서버를 리부팅하려는데 승인 화면에 영향 범위가 뜹니다. "5개 영향, 그중 체크아웃은 복제본 부족으로 다운."

**장애 중.** 데이터베이스가 죽었습니다. 무엇부터 확인해야 하는지 홉 순서로 나옵니다.

**온보딩.** 새로 온 사람이 위키 대신 `orrery blast`를 쳐 봅니다. 선배의 머릿속에만 있던 것이 명령 한 줄이 됩니다.

**에이전트 앞.** AI 에이전트에게 운영을 맡기기 전에, 그 행동의 결과를 먼저 계산해 봅니다. 4축 루브릭(되돌림·관측·경계·사람통제)이 `orrery.scoring`에 있습니다.

---

## 현재 상태

작동합니다. 테스트 15개 통과. 다만 초기 단계이고 정직하게 말하면 이렇습니다.

| 되는 것 | 아직 안 되는 것 |
|---|---|
| 개체·관계 모델, YAML 적재 | 실제 시스템 커넥터 (직접 써야 함) |
| 구조적 영향 범위 계산 | 시각화 (터미널 출력뿐) |
| 행동 모델 기반 결과 전파 | 시점 비교·스냅샷 버저닝 |
| 개체 해소 후보 제안 | 정확도 검증 (실제 장애 대조가 다음 과제) |
| 4축 신뢰 루브릭 | 가중치·심각도 (지금은 죽었나 살았나뿐) |

**가장 큰 미해결 문제는 정확도입니다.** 지도가 맞는지 어떻게 압니까. 다음 과제는 실제 장애 기록을 놓고 그때 이 계산이 맞췄을지 역채점하는 것입니다. 그 숫자가 나오기 전까지 이 도구의 답은 참고용입니다.

---

## 이름

orrery는 태양계 기계 모형입니다. 모든 행성이 모형 위에 있고, 크랭크를 돌리면 모든 위치가 계산됩니다.

> 모든 서버가 지도에 있고, 행동하면 결과가 계산된다.

---

## 더 읽기

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — 계층 구조와 설계 결정 (영어판, 이쪽이 최신 기준)
- [ARCHITECTURE.ko.md](docs/ARCHITECTURE.ko.md) — 같은 문서의 한국어판
- [CLEANROOM.md](CLEANROOM.md) — 이 리포에 들어가면 안 되는 것
- [CONTRIBUTING.md](CONTRIBUTING.md) — 기여 방법과 클린룸 규칙
- 라이선스: [Apache-2.0](LICENSE)
