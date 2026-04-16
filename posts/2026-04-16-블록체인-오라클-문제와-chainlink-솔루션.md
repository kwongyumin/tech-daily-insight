# 블록체인 오라클 문제와 Chainlink 솔루션

## 개요

스마트 컨트랙트는 강력한 자동화 도구지만, 치명적인 한계를 하나 갖고 있다. 블록체인은 근본적으로 **폐쇄된 결정론적 시스템**이기 때문에, 외부 세계의 데이터를 자체적으로 가져올 수 없다. ETH/USD 환율, 스포츠 경기 결과, 날씨 정보, 무작위 난수... 이 모든 것들은 체인 바깥에 존재한다.

이것이 바로 **오라클 문제(Oracle Problem)**다.

탈중앙화 금융(DeFi) 프로토콜이나 예측 시장, NFT 게임을 개발하다 보면 반드시 이 벽에 부딪히게 된다. "그냥 외부 API 호출하면 되지 않나?"라고 생각할 수 있지만, 스마트 컨트랙트 내부에서 HTTP 요청은 불가능하다. 모든 노드가 동일한 결과를 재현해야 하는 블록체인의 특성상, 비결정적인 외부 호출은 합의를 무너뜨린다.

이 글에서는 오라클 문제의 본질을 분석하고, 현재 업계 표준으로 자리잡은 **Chainlink**가 이를 어떻게 해결하는지 실전 코드와 함께 살펴본다.

---

## 핵심 개념

### 오라클 문제란 정확히 무엇인가?

오라클 문제는 단순히 "외부 데이터를 못 가져온다"는 기술적 제약이 아니다. 더 깊은 신뢰 문제를 내포한다.

```
[외부 세계] ──→ [오라클] ──→ [블록체인]
  실제 데이터     중간자       스마트 컨트랙트
```

만약 오라클이 단일 중앙화된 서버라면, 스마트 컨트랙트가 아무리 탈중앙화되어 있어도 **그 오라클 하나를 장악하면 전체 시스템이 무너진다**. "Garbage In, Garbage Out" 문제다. 2020년 Compound 프로토콜에서 DAI 가격 오라클이 조작되어 수천만 달러 규모의 청산이 발생한 사건이 대표적인 실례다.

오라클 문제의 핵심 과제는 세 가지다:

| 과제 | 설명 |
|------|------|
| **데이터 정확성** | 외부 데이터가 실제 값을 정확히 반영하는가 |
| **탈중앙화** | 단일 실패 지점(SPOF)이 존재하지 않는가 |
| **조작 저항성** | 악의적 행위자가 데이터를 조작할 수 없는가 |

### Chainlink의 아키텍처

Chainlink는 탈중앙화 오라클 네트워크(DON, Decentralized Oracle Network)를 통해 이 문제를 해결한다.

```
[데이터 소스 1] ─┐
[데이터 소스 2] ─┼→ [Chainlink Node 1] ─┐
[데이터 소스 3] ─┘                        │
                                          ├→ [온체인 집계] → [스마트 컨트랙트]
[데이터 소스 1] ─┐                        │
[데이터 소스 2] ─┼→ [Chainlink Node 2] ─┤
[데이터 소스 3] ─┘                        │
                                          │
[데이터 소스 1] ─┐                        │
[데이터 소스 2] ─┼→ [Chainlink Node N] ─┘
[데이터 소스 3] ─┘
```

핵심은 **다중 노드 + 다중 데이터 소스 + 온체인 집계**의 조합이다. 각 노드는 독립적으로 운영되며, 결과를 체인에 제출한다. 집계 과정에서 이상치는 걸러지고 중앙값(median)이 최종 답으로 채택된다.

Chainlink가 제공하는 주요 서비스는 다음과 같다:

- **Price Feeds**: 자산 가격 데이터 (DeFi에서 가장 많이 사용)
- **VRF (Verifiable Random Function)**: 검증 가능한 온체인 난수
- **Automation (Keepers)**: 조건 기반 자동 컨트랙트 실행
- **CCIP (Cross-Chain Interoperability Protocol)**: 크로스체인 메시지 전달
- **Functions**: 커스텀 API 연동

---

## 실전 예제

### 1. Price Feed 연동 - ETH/USD 가격 가져오기

가장 기본적인 활용 사례다. Chainlink Price Feed는 이미 집계된 데이터를 온체인에서 읽어오기만 하면 된다.

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import {AggregatorV3Interface} from "@chainlink/contracts/src/v0.8/shared/interfaces/AggregatorV3Interface.sol";

/**
 * @title PriceFeedConsumer
 * @notice ETH/USD 가격을 Chainlink Price Feed에서 읽어오는 컨트랙트
 */
contract PriceFeedConsumer {
    AggregatorV3Interface private immutable priceFeed;
    
    // Ethereum Mainnet: 0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419
    // Sepolia Testnet:  0x694AA1769357215DE4FAC081bf1f309aDC325306
    constructor(address _priceFeedAddress) {
        priceFeed = AggregatorV3Interface(_priceFeedAddress);
    }
    
    /**
     * @notice 최신 ETH/USD 가격 반환
     * @return price 8자리 소수점 기준 가격 (e.g., 200000000000 = $2000.00)
     * @return updatedAt 마지막 업데이트 타임스탬프
     */
    function getLatestPrice() public view returns (int256 price, uint256 updatedAt) {
        (
            uint80 roundId,
            int256 answer,
            uint256 startedAt,
            uint256 updateTime,
            uint80 answeredInRound
        ) = priceFeed.latestRoundData();
        
        // 스테일 데이터 검증 - 실무에서 반드시 필요
        require(answer > 0, "Invalid price: non-positive value");
        require(updateTime > 0, "Invalid round: not complete");
        require(answeredInRound >= roundId, "Stale price data");
        require(block.timestamp - updateTime <= 3600, "Price feed is stale"); // 1시간 이상 업데이트 없으면 거부
        
        return (answer, updateTime);
    }
    
    /**
     * @notice ETH 금액을 USD로 환산
     * @param ethAmount Wei 단위 ETH 금액
     * @return usdValue 8자리 소수점 기준 USD 가치
     */
    function ethToUsd(uint256 ethAmount) external view returns (uint256 usdValue) {
        (int256 price,) = getLatestPrice();
        uint8 decimals = priceFeed.decimals(); // 통상 8
        
        // ethAmount(18 decimals) * price(8 decimals) / 1e18 = result(8 decimals)
        usdValue = (ethAmount * uint256(price)) / (10 ** 18);
        return usdValue;
    }
}
```

### 2. VRF v2.5 - 검증 가능한 난수 생성

NFT 민팅 또는 게임 아이템 결정에 사용하는 패턴이다.

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import {VRFConsumerBaseV2Plus} from "@chainlink/contracts/src/v0.8/vrf/dev/VRFConsumerBaseV2Plus.sol";
import {VRFV2PlusClient} from "@chainlink/contracts/src/v0.8/vrf/dev/libraries/VRFV2PlusClient.sol";

/**
 * @title RandomNFTLottery
 * @notice VRF를 활용한 NFT 복권 컨트랙트
 */
contract RandomNFTLottery is VRFConsumerBaseV2Plus {
    // Sepolia VRF Coordinator
    address constant VRF_COORDINATOR = 0x9DdfaCa8183c41ad55329BdeeD9F6A8d53168B1b;
    
    bytes32 private immutable keyHash;
    uint256 private immutable subscriptionId;
    uint16 private constant REQUEST_CONFIRMATIONS = 3;
    uint32 private constant CALLBACK_GAS_LIMIT = 200_000;
    uint32 private constant NUM_WORDS = 1;
    
    mapping(uint256 requestId => address requester) public requestToSender;
    mapping(address user => uint256 randomResult) public userRandomResult;
    
    event RandomnessRequested(uint256 indexed requestId, address indexed requester);
    event RandomnessFulfilled(uint256 indexed requestId, uint256 randomWord);
    
    constructor(
        bytes32 _keyHash,
        uint256 _subscriptionId
    ) VRFConsumerBaseV2Plus(VRF_COORDINATOR) {
        keyHash = _keyHash;
        subscriptionId = _subscriptionId;
    }
    
    /**
     * @notice 난수 요청 - 트랜잭션 1
     */
    function requestRandomNumber() external returns (uint256 requestId) {
        requestId = s_vrfCoordinator.requestRandomWords(
            VRFV2PlusClient.RandomWordsRequest({
                keyHash: keyHash,
                subId: subscriptionId,
                requestConfirmations: REQUEST_CONFIRMATIONS,
                callbackGasLimit: CALLBACK_GAS_LIMIT,
                numWords: NUM_WORDS,
                extraArgs: VRFV2PlusClient._argsToBytes(
                    VRFV2PlusClient.ExtraArgsV1({nativePayment: false})
                )
            })
        );
        
        requestToSender[requestId] = msg.sender;
        emit RandomnessRequested(requestId, msg.sender);
    }
    
    /**
     * @notice Chainlink VRF가 콜백으로 호출 - 트랜잭션 2
     * @dev 이 함수는 VRF Coordinator만 호출 가능 (내부 검증됨)
     */
    function fulfillRandomWords(
        uint256 requestId,
        uint256[] calldata randomWords
    ) internal override {
        address requester = requestToSender[requestId];
        require(requester != address(0), "Request not found");
        
        // 0~9999 범위의 난수로 변환 (NFT 레어도 결정 등)
        uint256 normalizedRandom = randomWords[0] % 10000;
        userRandomResult[requester] = normalizedRandom;
        
        emit RandomnessFulfilled(requestId, normalizedRandom);
        
        // 후속 로직: 희귀도 결정
        _assignRarity(requester, normalizedRandom);
    }
    
    function _assignRarity(address user, uint256 randomValue) internal {
        // 0-4999: Common (50%), 5000-8499: Rare (35%), 
        // 8500-9799: Epic (13%), 9800-9999: Legendary (2%)
        if (randomValue < 5000) {
            // mint Common NFT
        } else if (randomValue < 8500) {
            // mint Rare NFT  
        } else if (randomValue < 9800) {
            // mint Epic NFT
        } else {
            // mint Legendary NFT
        }
    }
}
```

### 3. Hardhat 테스트 환경 설정

실제 개발 시 로컬 테스트를 위한 Mock 설정이다.

```javascript
// test/PriceFeedConsumer.test.js
const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("PriceFeedConsumer", function () {
  let priceFeedConsumer;
  let mockV3Aggregator;
  
  const INITIAL_PRICE = 200000000000n; // $2000 with 8 decimals
  const DECIMALS = 8;

  beforeEach(async function () {
    // Mock Aggregator 배포 (로컬 테스트용)
    const MockV3AggregatorFactory = await ethers.getContractFactory(
      "MockV3Aggregator"
    );
    mockV3Aggregator = await MockV3AggregatorFactory.deploy(
      DECIMALS,
      INITIAL_PRICE
    );

    const PriceFeedConsumerFactory = await ethers.getContractFactory(
      "PriceFeedConsumer"
    );
    priceFeedConsumer = await PriceFeedConsumerFactory.deploy(
      await mockV3Aggregator.getAddress()
    );
  });

  it("정상적인 ETH/USD 가격을 반환해야 한다", async function () {
    const [price] = await priceFeedConsumer.getLatestPrice();
    expect(price).to.equal(INITIAL_PRICE);
  });

  it("ETH를 USD로 정확히 환산해야 한다", async function () {
    const oneEth = ethers.parseEther("1");
    const usdValue = await priceFeedConsumer.ethToUsd(oneEth);
    // 1 ETH * $2000 = 200000000000 (8 decimals)
    expect(usdValue).to.equal(INITIAL_PRICE);
  });

  it("스테일 데이터는 거부해야 한다", async function () {
    // Mock에서 오래된 타임스탬프 시뮬레이션
    await mockV3Aggregator.updateRoundData(
      2n,
      INITIAL_PRICE,
      BigInt(Math.floor(Date.now() / 1000) - 7200), // 2시간 전
      BigInt(Math.floor(Date.now() / 1000) - 7200),
      2n
    );
    
    await expect(priceFeedConsumer.getLatestPrice())
      .to.be.revertedWith("Price feed is stale");
  });
});
```

---

## 주의사항 및 트레이드오프

### 반드시 챙겨야 할 보안 체크리스트

**1. 스테일 데이터 검증 (가장 중요)**
```solidity
// ❌ 잘못된 방법 - 검증 없음
(, int256 price,,,) = priceFeed.latestRoundData();

// ✅ 올바른 방법 - 모든 필드 검증
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) 
    = priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale round");
require(block.timestamp - updatedAt < STALENESS_THRESHOLD, "Stale price");
```

**2. 가격 조작 방어 - TWAP 활용**
스팟 가격만 사용하면 플래시론 공격에 취약하다. 고가치 DeFi 프로토콜이라면 Chainlink Price Feed와 함께 시간 가중 평균 가격(TWAP)을 병행 검토해야 한다.

**3. VRF 콜백 Gas Limit 설정**
`CALLBACK_GAS_LIMIT`을 너무 작게 설정하면 콜백이 실패하고, 요청한 LINK가 소모된 채로 난수를 받지 못한다. 충분한 가스를 설정하고, 콜백 함수를 최대한 가볍게 유지해야 한다.

### 트레이드오프 분석

| 항목 | 내용 |
|------|------|
| **비용** | LINK 토큰 소모 (VRF, Functions), Price Feed는 무료 |
| **지연** | VRF는 2트랜잭션 패턴 - 요청과 응답 사이 수십 초~수 분 지연 |
| **신뢰 가정** | 완전한 탈중앙화는 아님 - Chainlink Labs에 대한 신뢰가 내포됨 |
| **체인 지원** | 메이저 체인은 지원하나 일부 L2/사이드체인 미지원 |
| **경쟁자** | Band Protocol, API3, UMA 등 - 특정 상황에서는 대안이 더 적합할 수 있음 |

### Chainlink를 쓰지 말아야 할 상황

- **완전한 온체인 난수**가 필요하다면: VRF는 결국 외부 의존성이 있다. 완전 온체인 환경에서는 commit-reveal 패턴 검토
- **초저지연 가격 데이터**: Chainlink는 편차 임계값(Deviation Threshold) 기반으로 업데이트되어 수 분 지연이 발생할 수 있음. 고빈도 거래 프로토콜은 Pyth Network 고려
- **Chainlink 미지원 체인**: API3의 dAPI 또는 자체 오라클 구현 검토

---

## 정리

오라클 문제는 블록체인의 구조적 한계에서 비롯된 근본적인 과제다. 이를 단순히 "외부 데이터 연동 문제"로 축소하면 심각한 보안 취약점을 만들 수 있다.

Chainlink는 다음 세 가지 원칙으로 이 문제를 현실적으로 해결한다:

1. **탈중앙화** - 다수의 독립 노드가 합의를 통해 데이터 제공
2. **암호경제학적 인센티브** - 노드 운영자가 올바른 데이터를 제출하도록 LINK 스테이킹으로 동기 부여
3. **투명성** - 모든 데이터 출처와 집계 과정이 온체인에서 검증 가능

실무에서 DeFi 프로토콜을 개발한다면 Price Feed를, 게임이나 NFT를 개발한다면 VRF를 우선적으로 고려하자. 단, 스테일 데이터 검증과 같은 기본 보안 체크리스트는 어떤 상황에서도 생략하지 말아야 한다.

완벽한 오라클은 존재하지 않는다. 어떤 오라클을 선택하든 신뢰 가정이 개입된다. 중요한 것은 그 신뢰 가정을 명확히 이해하고, 프로토콜 설계에 반영하는 것이다.

> **참고 자료**
> - [Chainlink 공식 문서](https://docs.chain.link/)
> - [Chainlink Price Feed 컨트랙트 주소 목록](https://docs.chain.link/data-feeds/price-feeds/addresses)
> - [Chainlink VRF v2.5 가이드](https://docs.chain.link/vrf)
> - [SmartContract Security: Oracle Manipulation](https://consensys.github.io/smart-contract-best-practices/attacks/oracle-manipulation/)