# Hyperledger Fabric 기업용 블록체인 네트워크 구축

## 개요

기업 환경에서 블록체인 기술을 도입할 때, 퍼블릭 블록체인(Bitcoin, Ethereum)은 성능 한계, 프라이버시 문제, 거버넌스 이슈로 인해 적합하지 않은 경우가 많다. **Hyperledger Fabric**은 Linux Foundation 산하 Hyperledger 프로젝트의 핵심 프레임워크로, 허가형(Permissioned) 블록체인 네트워크를 구축할 수 있게 해준다.

공급망 관리, 금융 거래 추적, 디지털 자산 관리 등 실제 엔터프라이즈 환경에서 활발히 사용되고 있으며, 채널(Channel) 기반의 데이터 격리, 플러그어블 합의 알고리즘, Go/Java/Node.js 기반 체인코드(Chaincode) 지원 등으로 유연한 아키텍처 설계가 가능하다.

이 포스팅에서는 Hyperledger Fabric의 핵심 아키텍처를 이해하고, 실제 네트워크를 구축하여 체인코드를 배포·호출하는 전 과정을 다룬다.

---

## 핵심 개념

### 아키텍처 구성 요소

Hyperledger Fabric의 주요 구성 요소를 먼저 파악해야 한다.

| 구성 요소 | 역할 |
|---|---|
| **Peer** | 원장(Ledger)을 보유하고 체인코드를 실행하는 노드 |
| **Orderer** | 트랜잭션 순서를 보장하고 블록을 생성 (Raft 기반) |
| **CA (Certificate Authority)** | MSP(Membership Service Provider) 인증서 발급 |
| **Channel** | 특정 조직 간의 프라이빗 통신 채널 |
| **Chaincode** | 스마트 컨트랙트. 비즈니스 로직을 담당 |
| **Ledger** | World State(CouchDB/LevelDB) + Blockchain(블록 로그) |

### 트랜잭션 흐름 (Execute-Order-Validate)

Fabric의 트랜잭션 처리는 3단계로 이루어진다.

```
[Client] → Proposal 전송
    ↓
[Endorsing Peers] → 체인코드 시뮬레이션 → Read/Write Set 반환
    ↓
[Client] → Endorsement 수집 후 Orderer로 전송
    ↓
[Orderer] → 트랜잭션 정렬 → 블록 생성
    ↓
[All Peers] → 블록 검증 → 원장 커밋
```

이 구조 덕분에 체인코드 실행이 합의 과정과 분리되어 **높은 처리량**을 달성할 수 있다.

### MSP와 채널 정책

조직(Org)마다 독립적인 MSP를 가지며, 채널 내 정책(Policy)은 `AND`, `OR`, `NOutOf` 조합으로 정의한다.

```yaml
# configtx.yaml 정책 예시
Policies:
  Endorsement:
    Type: Signature
    Rule: "AND('Org1MSP.peer', 'Org2MSP.peer')"
  LifecycleEndorsement:
    Type: Signature
    Rule: "OR('Org1MSP.peer', 'Org2MSP.peer')"
```

---

## 실전 예제

### 1. 로컬 네트워크 구성 (Docker Compose)

2개 조직(Org1, Org2), 1개 Orderer, 채널 1개로 구성된 최소 네트워크를 설정한다.

```yaml
# docker-compose.yaml (일부 발췌)
version: '3.7'

networks:
  fabric_network:
    driver: bridge

services:
  orderer.example.com:
    image: hyperledger/fabric-orderer:2.5
    environment:
      - ORDERER_GENERAL_LISTENADDRESS=0.0.0.0
      - ORDERER_GENERAL_LISTENPORT=7050
      - ORDERER_GENERAL_BOOTSTRAPMETHOD=none
      - ORDERER_CHANNELPARTICIPATION_ENABLED=true
      - ORDERER_ADMIN_LISTENADDRESS=0.0.0.0:7053
      - ORDERER_GENERAL_LOCALMSPID=OrdererMSP
      - ORDERER_GENERAL_LOCALMSPDIR=/var/hyperledger/orderer/msp
      - ORDERER_CONSENSUS_TYPE=etcdraft
    volumes:
      - ./crypto-config/ordererOrganizations/example.com/orderers/orderer.example.com/msp:/var/hyperledger/orderer/msp
      - ./channel-artifacts:/var/hyperledger/production/orderer
    ports:
      - 7050:7050
      - 7053:7053
    networks:
      - fabric_network

  peer0.org1.example.com:
    image: hyperledger/fabric-peer:2.5
    environment:
      - CORE_PEER_ID=peer0.org1.example.com
      - CORE_PEER_ADDRESS=peer0.org1.example.com:7051
      - CORE_PEER_LISTENADDRESS=0.0.0.0:7051
      - CORE_PEER_LOCALMSPID=Org1MSP
      - CORE_PEER_MSPCONFIGPATH=/etc/hyperledger/fabric/msp
      - CORE_LEDGER_STATE_STATEDATABASE=CouchDB
      - CORE_LEDGER_STATE_COUCHDBCONFIG_COUCHDBADDRESS=couchdb0:5984
    volumes:
      - ./crypto-config/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/msp:/etc/hyperledger/fabric/msp
    ports:
      - 7051:7051
    depends_on:
      - couchdb0
    networks:
      - fabric_network

  couchdb0:
    image: couchdb:3.3.3
    environment:
      - COUCHDB_USER=admin
      - COUCHDB_PASSWORD=adminpw
    ports:
      - 5984:5984
    networks:
      - fabric_network
```

### 2. 인증서 및 채널 생성

```bash
# cryptogen으로 인증서 생성
cryptogen generate --config=./crypto-config.yaml --output="crypto-config"

# configtxgen으로 제네시스 블록 생성
configtxgen -profile TwoOrgsOrdererGenesis \
  -channelID system-channel \
  -outputBlock ./channel-artifacts/genesis.block

# 채널 트랜잭션 생성
configtxgen -profile TwoOrgsChannel \
  -outputCreateChannelTx ./channel-artifacts/mychannel.tx \
  -channelID mychannel

# 앵커 피어 업데이트 트랜잭션 생성
configtxgen -profile TwoOrgsChannel \
  -outputAnchorPeersUpdate ./channel-artifacts/Org1MSPanchors.tx \
  -channelID mychannel \
  -asOrg Org1MSP
```

### 3. Go 체인코드 작성

자산 관리 체인코드 예제다. Fabric Contract API를 사용한다.

```go
// chaincode/asset/asset.go
package main

import (
    "encoding/json"
    "fmt"
    "log"

    "github.com/hyperledger/fabric-contract-api-go/contractapi"
)

type AssetContract struct {
    contractapi.Contract
}

type Asset struct {
    ID       string `json:"id"`
    Owner    string `json:"owner"`
    Value    int    `json:"value"`
    OrgID    string `json:"orgId"`
    UpdatedAt string `json:"updatedAt"`
}

// CreateAsset - 새 자산 생성
func (a *AssetContract) CreateAsset(ctx contractapi.TransactionContextInterface,
    id, owner string, value int) error {

    // 중복 체크
    existing, err := ctx.GetStub().GetState(id)
    if err != nil {
        return fmt.Errorf("상태 조회 실패: %w", err)
    }
    if existing != nil {
        return fmt.Errorf("자산 %s 이미 존재합니다", id)
    }

    // MSP ID 추출 (호출 조직 확인)
    mspID, err := ctx.GetClientIdentity().GetMSPID()
    if err != nil {
        return fmt.Errorf("MSP ID 조회 실패: %w", err)
    }

    asset := Asset{
        ID:       id,
        Owner:    owner,
        Value:    value,
        OrgID:    mspID,
        UpdatedAt: ctx.GetStub().GetTxTimestamp().AsTime().String(),
    }

    assetJSON, err := json.Marshal(asset)
    if err != nil {
        return err
    }

    return ctx.GetStub().PutState(id, assetJSON)
}

// TransferAsset - 자산 소유권 이전
func (a *AssetContract) TransferAsset(ctx contractapi.TransactionContextInterface,
    id, newOwner string) (*Asset, error) {

    asset, err := a.ReadAsset(ctx, id)
    if err != nil {
        return nil, err
    }

    // 호출자 검증 - 현재 소유 조직만 이전 가능
    mspID, _ := ctx.GetClientIdentity().GetMSPID()
    if asset.OrgID != mspID {
        return nil, fmt.Errorf("권한 없음: %s 조직은 이 자산을 이전할 수 없습니다", mspID)
    }

    asset.Owner = newOwner
    asset.UpdatedAt = ctx.GetStub().GetTxTimestamp().AsTime().String()

    assetJSON, err := json.Marshal(asset)
    if err != nil {
        return nil, err
    }

    if err := ctx.GetStub().PutState(id, assetJSON); err != nil {
        return nil, err
    }

    // 이벤트 발행
    _ = ctx.GetStub().SetEvent("AssetTransferred",
        []byte(fmt.Sprintf(`{"id":"%s","newOwner":"%s"}`, id, newOwner)))

    return asset, nil
}

// ReadAsset - 자산 조회
func (a *AssetContract) ReadAsset(ctx contractapi.TransactionContextInterface,
    id string) (*Asset, error) {

    assetJSON, err := ctx.GetStub().GetState(id)
    if err != nil {
        return nil, fmt.Errorf("원장 읽기 실패: %w", err)
    }
    if assetJSON == nil {
        return nil, fmt.Errorf("자산 %s를 찾을 수 없습니다", id)
    }

    var asset Asset
    if err := json.Unmarshal(assetJSON, &asset); err != nil {
        return nil, err
    }
    return &asset, nil
}

// QueryAssetsByOwner - CouchDB Rich Query
func (a *AssetContract) QueryAssetsByOwner(ctx contractapi.TransactionContextInterface,
    owner string) ([]*Asset, error) {

    queryString := fmt.Sprintf(`{
        "selector": {
            "owner": "%s"
        },
        "sort": [{"updatedAt": "desc"}]
    }`, owner)

    resultsIterator, err := ctx.GetStub().GetQueryResult(queryString)
    if err != nil {
        return nil, err
    }
    defer resultsIterator.Close()

    var assets []*Asset
    for resultsIterator.HasNext() {
        queryResult, err := resultsIterator.Next()
        if err != nil {
            return nil, err
        }

        var asset Asset
        if err := json.Unmarshal(queryResult.Value, &asset); err != nil {
            return nil, err
        }
        assets = append(assets, &asset)
    }
    return assets, nil
}

func main() {
    assetChaincode, err := contractapi.NewChaincode(&AssetContract{})
    if err != nil {
        log.Fatalf("체인코드 생성 실패: %v", err)
    }
    if err := assetChaincode.Start(); err != nil {
        log.Fatalf("체인코드 시작 실패: %v", err)
    }
}
```

### 4. 체인코드 배포 (Fabric 2.x Lifecycle)

Fabric 2.x부터는 탈중앙화된 체인코드 라이프사이클이 적용된다.

```bash
# 패키징
peer lifecycle chaincode package asset.tar.gz \
  --path ./chaincode/asset \
  --lang golang \
  --label asset_1.0

# 각 조직에서 설치
peer lifecycle chaincode install asset.tar.gz

# Package ID 확인
PACKAGE_ID=$(peer lifecycle chaincode queryinstalled \
  --output json | jq -r '.installed_chaincodes[0].package_id')

# Org1에서 승인
peer lifecycle chaincode approveformyorg \
  --channelID mychannel \
  --name asset \
  --version 1.0 \
  --package-id $PACKAGE_ID \
  --sequence 1 \
  --orderer orderer.example.com:7050 \
  --tls --cafile $ORDERER_CA

# Org2에서도 동일하게 승인 후 커밋
peer lifecycle chaincode commit \
  --channelID mychannel \
  --name asset \
  --version 1.0 \
  --sequence 1 \
  --orderer orderer.example.com:7050 \
  --tls --cafile $ORDERER_CA \
  --peerAddresses peer0.org1.example.com:7051 \
  --peerAddresses peer0.org2.example.com:9051

# 체인코드 호출 테스트
peer chaincode invoke \
  -C mychannel -n asset \
  -c '{"function":"CreateAsset","Args":["asset1","Alice","1000"]}' \
  --orderer orderer.example.com:7050 \
  --tls --cafile $ORDERER_CA
```

### 5. Java SDK를 통한 클라이언트 연동

Spring Boot 환경에서 Fabric Gateway SDK로 체인코드를 호출하는 예제다.

```java
// FabricGatewayService.java
@Service
@Slf4j
public class FabricGatewayService {

    private Gateway gateway;
    private Network network;

    @PostConstruct
    public void init() throws Exception {
        Path walletPath = Paths.get("wallet");
        Wallet wallet = Wallets.newFileSystemWallet(walletPath);

        Path networkConfigPath = Paths.get("connection-org1.yaml");

        Gateway.Builder builder = Gateway.createBuilder()
            .identity(wallet, "appUser")
            .networkConfig(networkConfigPath)
            .discovery(true);

        this.gateway = builder.connect();
        this.network = gateway.getNetwork("mychannel");
    }

    public String createAsset(String id, String owner, int value) throws Exception {
        Contract contract = network.getContract("asset");

        byte[] result = contract.submitTransaction(
            "CreateAsset", id, owner, String.valueOf(value)
        );
        log.info("자산 생성 완료: {}", new String(result));
        return new String(result);
    }

    public Asset readAsset(String id) throws Exception {
        Contract contract = network.getContract("asset");

        // evaluateTransaction은 원장에 쓰지 않음 (쿼리 전용)
        byte[] result = contract.evaluateTransaction("ReadAsset", id);

        ObjectMapper mapper = new ObjectMapper();
        return mapper.readValue(result, Asset.class);
    }

    // 이벤트 리스너 등록
    public void registerAssetTransferListener() {
        Contract contract = network.getContract("asset");
        contract.addContractListener(event -> {
            if ("AssetTransferred".equals(event.getName())) {
                log.info("자산 이전 이벤트 감지: {}", new String(event.getPayload()));
                // 후속 처리 로직
            }
        });
    }

    @PreDestroy
    public void close() {
        if (gateway != null) gateway.close();
    }
}
```

---

## 주의사항 및 트레이드오프

### ⚠️ 성능 병목 포인트

**Orderer가 단일 장애점(SPOF)이 될 수 있다.** Raft 기반 Orderer 클러스터는 최소 3개 노드 이상(홀수) 구성을 권장한다. TPS는 체인코드 복잡도, 블록 크기 설정(`BatchSize`, `BatchTimeout`), 피어 수에 크게 좌우된다. 일반적으로 단순 트랜잭션 기준 수백~수천 TPS 수준이며, 이더리움 대비 훨씬 빠르지만 기존 RDBMS와는 비교할 수 없다.

### ⚠️ 운영 복잡도

- 인증서 만료 관리가 핵심이다. MSP 인증서 갱신 절차를 반드시 자동화해야 한다.
- 채널 설정 변경(configtx)은 절차가 복잡하고 모든 조직의 서명이 필요하다.
- Docker 기반 체인코드 실행은 컨테이너 레지스트리 관리가 필요하다.

### ⚠️ CouchDB vs LevelDB

| 항목 | LevelDB | CouchDB |
|---|---|---|
| Rich Query | 불가 | 가능 (JSON) |
| 성능 | 빠름 | 상대적으로 느림 |
| 운영 복잡도 | 낮음 | 높음 |
| 인덱스 관리 | 불필요 | 필수 |

Rich Query가 필요한 경우 CouchDB를 선택하되, **반드시 인덱스를 정의**해야 한다. 인덱스 없는 Full Scan은 운영 환경에서 심각한 성능 저하를 유발한다.

```json
// META-INF/statedb/couchdb/indexes/indexOwner.json
{
  "index": {
    "fields": ["owner", "updatedAt"]
  },
  "ddoc": "indexOwnerDoc",
  "name": "indexOwner",
  "type": "json"
}
```

### ⚠️ Private Data Collection

채널 내에서도 특정 조직 간 데이터를 격리해야 한다면 채널을 추가로 만들기보다 **Private Data Collection**을 활용하는 것이 더 효율적이다. 실제 데이터는 해시만 원장에 기록되고, 원본 데이터는 권한 있는 피어에만 전달된다.

---

## 정리

Hyperledger Fabric은 기업 간 신뢰가 보장된 데이터 공유와 비즈니스 프로세스 자동화에 탁월한 플랫폼이다. 핵심 설계 철학인 **모듈화(Modularity)**와 **허가형 접근제어**는 컴플라이언스 요구사항이 강한 금융·의료·물류 도메인에 특히 적합하다.

실무 도입 시 체크리스트:

- [ ] 채널 설계: 데이터 격리 단위를 채널로 할지 Private Data로 할지 결정
- [ ] CA 인프라 설계: Fabric CA vs 기존 엔터프라이즈 CA 통합
- [ ] Orderer 클러스터: 최소 3노드 Raft 구성
- [ ] State DB 선택: 쿼리 패턴에 따라 LevelDB/CouchDB 결정
- [ ] 인증서 수명 주기 관리 자동화
- [ ] 모니터링: Prometheus + Grafana를 통한 피어/오더러 메트릭 수집

Hyperledger Fabric은 학습 곡선이 가파르지만, 아키텍처를 깊이 이해하고 나면 기업의 복잡한 비즈니스 요구사항을 블록체인 기반으로 구현할 수 있는 강력한 도구가 된다.