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

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — 8개 계층과 설계 결정
- [CLEANROOM.md](CLEANROOM.md) — 이 리포에 들어가면 안 되는 것
- 라이선스: Apache-2.0
