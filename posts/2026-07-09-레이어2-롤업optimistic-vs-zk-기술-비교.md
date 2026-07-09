# 레이어2 롤업(Optimistic vs ZK) 기술 비교

## 개요

이더리움 메인넷(레이어1)의 고질적인 문제인 낮은 처리량(TPS)과 높은 가스비를 해결하기 위한 가장 현실적인 솔루션으로 **레이어2 롤업(Layer 2 Rollup)** 이 자리잡고 있다. 롤업은 트랜잭션을 오프체인에서 처리한 뒤 결과를 레이어1에 압축하여 기록함으로써 보안성은 유지하면서 비용과 속도를 대폭 개선한다.

현재 롤업 진영은 크게 두 가지로 나뉜다:

- **Optimistic Rollup**: Arbitrum, Optimism (OP Stack), Base
- **ZK Rollup (Zero-Knowledge Rollup)**: zkSync Era, Polygon zkEVM, StarkNet, Scroll

두 방식 모두 레이어1 보안을 상속받지만 증명 방식, 최종성(Finality), 개발 복잡도 등에서 근본적인 차이가 있다. 이 글에서는 두 기술의 핵심 원리를 비교하고 실무에서 어떤 선택 기준을 가져가야 하는지 살펴본다.

---

## 핵심 개념

### Optimistic Rollup의 동작 원리

Optimistic Rollup은 이름 그대로 **낙관적(Optimistic)** 가정에서 출발한다. 오프체인에서 실행된 트랜잭션이 유효하다고 가정하고 레이어1에 데이터를 게시한 뒤, 일정 기간(보통 **7일**)의 **챌린지 기간(Challenge Period)** 을 둔다. 이 기간 동안 누구든 사기 증명(Fraud Proof)을 제출하여 잘못된 상태 전이를 검증할 수 있다.

```
[사용자 트랜잭션]
      │
      ▼
[Sequencer (오프체인 실행)]
      │
      ▼
[State Root + Call Data → L1 게시]
      │
      ▼
[챌린지 기간 (7일)] ←── Verifier가 Fraud Proof 제출 가능
      │
      ▼
[최종 확정 (Finality)]
```

챌린지가 없으면 해당 상태는 최종 확정된다. 이 구조 덕분에 EVM 호환성이 높고 구현이 상대적으로 단순하다.

### ZK Rollup의 동작 원리

ZK Rollup은 **영지식 증명(Zero-Knowledge Proof)** 을 사용하여 트랜잭션 실행의 유효성을 수학적으로 즉시 증명한다. Sequencer가 트랜잭션 배치를 처리하고 **유효성 증명(Validity Proof, SNARK/STARK)** 을 생성하여 레이어1 검증자 컨트랙트에 제출한다.

```
[사용자 트랜잭션]
      │
      ▼
[Sequencer (오프체인 실행)]
      │
      ▼
[Prover: ZK Proof 생성] ← 수십 초 ~ 수 분 소요
      │
      ▼
[Proof + State Root → L1 검증 컨트랙트]
      │
      ▼
[검증 즉시 최종 확정 (Finality)]
```

챌린지 기간이 없으므로 빠른 최종성을 제공하지만, 증명 생성(Proving) 단계의 연산 비용이 크다.

### 핵심 비교표

| 항목 | Optimistic Rollup | ZK Rollup |
|------|-------------------|-----------|
| 증명 방식 | Fraud Proof (사후 검증) | Validity Proof (사전 수학 증명) |
| 최종성 | ~7일 (챌린지 기간) | 수 분 ~ 수십 분 |
| EVM 호환성 | 완전 호환 (EVM-equivalent) | 제한적 → 점차 개선 중 |
| 증명 비용 | 낮음 | 높음 (Prover 연산) |
| 보안 가정 | 최소 1명의 정직한 감시자 | 수학적 증명 (신뢰 불필요) |
| 데이터 가용성 | Calldata / EIP-4844 Blob | Calldata / EIP-4844 Blob |
| 대표 프로젝트 | Arbitrum One, Base | zkSync Era, Scroll |

---

## 실전 예제

### 예제 1: Optimistic Rollup — Arbitrum에서 L1 ↔ L2 메시지 패싱

Arbitrum의 `Inbox` 컨트랙트를 통해 L1에서 L2로 메시지를 보내는 예시다. 실무에서 L1 이벤트를 트리거로 L2 컨트랙트를 호출할 때 자주 쓰인다.

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

interface IInbox {
    function createRetryableTicket(
        address destAddr,
        uint256 arbTxCallValue,
        uint256 maxSubmissionCost,
        address submissionRefundAddress,
        address valueRefundAddress,
        uint256 gasLimit,
        uint256 maxFeePerGas,
        bytes calldata data
    ) external payable returns (uint256);
}

contract L1ToL2Sender {
    address public immutable inbox;

    constructor(address _inbox) {
        inbox = _inbox;
    }

    /// @notice L1에서 L2 컨트랙트 함수를 호출하는 Retryable Ticket 생성
    function sendMessageToL2(
        address l2Target,
        bytes calldata data,
        uint256 maxSubmissionCost,
        uint256 gasLimit,
        uint256 maxFeePerGas
    ) external payable returns (uint256 ticketId) {
        ticketId = IInbox(inbox).createRetryableTicket{value: msg.value}(
            l2Target,
            0,                    // L2에 전송할 ETH 없음
            maxSubmissionCost,
            msg.sender,           // 수수료 환불 주소
            msg.sender,
            gasLimit,
            maxFeePerGas,
            data
        );
    }
}
```

```javascript
// ethers.js v6 - Retryable Ticket 상태 모니터링
import { ethers } from "ethers";
import { L1TransactionReceipt, L1ToL2MessageStatus } from "@arbitrum/sdk";

async function waitForL2Execution(l1TxHash, l1Provider, l2Provider) {
  const l1Receipt = await l1Provider.getTransactionReceipt(l1TxHash);
  const l1TxReceipt = new L1TransactionReceipt(l1Receipt);

  const messages = await l1TxReceipt.getL1ToL2Messages(l2Provider);
  const message = messages[0];

  console.log("L2 메시지 상태 대기 중...");
  const result = await message.waitForStatus();

  if (result.status === L1ToL2MessageStatus.REDEEMED) {
    console.log("✅ L2 실행 완료:", result.l2TxReceipt.transactionHash);
  } else {
    console.log("⚠️ 상태:", result.status);
    // 필요 시 수동 redeem 가능
    await message.redeem();
  }
}
```

### 예제 2: ZK Rollup — zkSync Era에서 Paymaster 활용

zkSync의 강점 중 하나인 **네이티브 Account Abstraction**을 활용한 Paymaster 예제다. 가스비를 ERC-20 토큰으로 대납하거나 무료로 제공할 수 있다.

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IPaymaster, ExecutionResult, PAYMASTER_VALIDATION_SUCCESS_MAGIC}
    from "@matterlabs/zksync-contracts/l2/system-contracts/interfaces/IPaymaster.sol";
import {IPaymasterFlow} from 
    "@matterlabs/zksync-contracts/l2/system-contracts/interfaces/IPaymasterFlow.sol";

contract ERC20Paymaster is IPaymaster {
    address public immutable allowedToken;
    uint256 public constant TOKEN_PER_TX = 1e18; // 트랜잭션당 1 토큰

    constructor(address _token) {
        allowedToken = _token;
    }

    function validateAndPayForPaymasterTransaction(
        bytes32,
        bytes32,
        Transaction calldata _transaction
    ) external payable returns (bytes4 magic, bytes memory) {
        magic = PAYMASTER_VALIDATION_SUCCESS_MAGIC;

        address userAddress = address(uint160(_transaction.from));

        // 사용자로부터 ERC-20 토큰 수령
        IERC20(allowedToken).transferFrom(
            userAddress,
            address(this),
            TOKEN_PER_TX
        );

        // 부트로더에게 ETH 가스비 지불
        uint256 requiredETH = _transaction.gasLimit * _transaction.maxFeePerGas;
        (bool success,) = payable(BOOTLOADER_FORMAL_ADDRESS).call{
            value: requiredETH
        }("");
        require(success, "가스비 지불 실패");
    }

    function postTransaction(
        bytes calldata,
        Transaction calldata,
        bytes32,
        bytes32,
        ExecutionResult,
        uint256
    ) external payable override {}

    address constant BOOTLOADER_FORMAL_ADDRESS = 
        0x0000000000000000000000000000000000008001;
}
```

```typescript
// zkSync SDK로 Paymaster 트랜잭션 전송
import { Provider, Wallet, utils } from "zksync-ethers";

async function sendWithPaymaster(
  wallet: Wallet,
  contractAddress: string,
  calldata: string,
  paymasterAddress: string,
  tokenAddress: string
) {
  const provider = new Provider("https://mainnet.era.zksync.io");

  const paymasterParams = utils.getPaymasterParams(paymasterAddress, {
    type: "ApprovalBased",
    token: tokenAddress,
    minimalAllowance: BigInt("1000000000000000000"), // 1 Token
    innerInput: new Uint8Array(),
  });

  const tx = await wallet.sendTransaction({
    to: contractAddress,
    data: calldata,
    customData: {
      gasPerPubdata: utils.DEFAULT_GAS_PER_PUBDATA_LIMIT,
      paymasterParams,
    },
  });

  const receipt = await tx.wait();
  console.log("✅ ZK Rollup 트랜잭션 확정:", receipt.hash);
  // ZK Proof 검증 후 즉시 최종 확정 — 7일 대기 불필요
}
```

---

## 주의사항 및 트레이드오프

### 1. 출금(Withdrawal) 지연 문제 — Optimistic의 아킬레스건

Optimistic Rollup의 7일 챌린지 기간은 L2 → L1 자산 이동 시 **7일 대기**를 의미한다. 이를 해결하기 위해 **유동성 브리지(Fast Bridge)** 를 활용하지만, 추가 수수료와 스마트 컨트랙트 리스크가 따른다.

```
일반 출금: L2 → (7일 대기) → L1  
Fast Bridge: L2 → LP가 L1에서 즉시 지급 → LP는 7일 후 정산
```

**실무 팁**: 사용자 경험(UX)을 위해 Across, Hop Protocol, Stargate 같은 서드파티 브릿지를 통합하되, 브릿지 컨트랙트 감사(Audit) 여부를 반드시 확인해야 한다.

### 2. ZK Proof 생성 비용과 증명 시간

ZK Rollup의 Prover는 막대한 연산 자원을 소모한다. 소규모 트랜잭션 배치는 증명 비용이 상대적으로 더 비싸지므로 **배치 크기 최적화**가 핵심이다.

| 구분 | 증명 방식 | 검증 가스 | EVM 지원 |
|------|-----------|-----------|----------|
| zkSync Era | SNARK (Boojum) | ~500K gas | zkEVM |
| StarkNet | STARK | ~5M gas | Cairo VM |
| Scroll | SNARK | ~500K gas | EVM-equivalent |
| Polygon zkEVM | SNARK | ~750K gas | EVM-equivalent |

STARK는 신뢰 설정(Trusted Setup)이 불필요하나 증명 크기가 크다. SNARK는 증명 크기가 작고 검증 비용이 낮지만 초기 신뢰 설정이 필요하다.

### 3. Sequencer 중앙화 리스크

현재 대부분의 롤업은 **단일 Sequencer** 구조다. Sequencer 장애 시 네트워크가 멈출 수 있고, MEV 추출과 트랜잭션 검열 문제가 발생할 수 있다. Arbitrum, Optimism 모두 탈중앙화 Sequencer 로드맵을 발표했지만 아직 진행 중이다.

### 4. EIP-4844(Proto-Danksharding)의 영향

2024년 3월 적용된 EIP-4844는 **Blob 트랜잭션**을 도입하여 롤업의 데이터 가용성 비용을 평균 **10~100배** 절감했다. Blob 데이터는 약 18일 후 삭제되므로 영구 저장이 필요한 데이터는 별도 처리가 필요하다.

```
기존 Calldata 비용: ~16 gas/byte
Blob 데이터 비용: ~1 gas/byte (초기 목표)
→ Arbitrum, Optimism 모두 EIP-4844 적용 후 수수료 대폭 감소
```

---

## 정리

두 기술 모두 빠르게 성숙하고 있으며, 선택 기준은 **사용 목적과 요구사항**에 따라 달라진다.

**Optimistic Rollup을 선택해야 할 때:**
- 기존 EVM 컨트랙트를 수정 없이 그대로 배포해야 할 때
- 즉각적인 L2 → L1 출금이 크리티컬하지 않은 서비스
- 개발/디버깅 환경이 이더리움과 완전히 동일해야 할 때

**ZK Rollup을 선택해야 할 때:**
- 빠른 최종성이 필요한 결제, 거래소 서비스
- Account Abstraction 기반 UX 혁신이 필요할 때
- 장기적으로 수학적 보안 보장을 우선시할 때

장기 트렌드는 **ZK 기술의 완성도가 높아지면서 Optimistic 영역을 점진적으로 대체**하는 방향이다. 특히 zkEVM의 EVM 호환성이 완전해지는 시점이 분기점이 될 것이다. 실무에서는 당장의 개발 생산성(Optimistic)과 미래의 기술 방향성(ZK) 사이의 균형을 고려한 선택이 필요하다.

> **참고 자료**
> - [L2Beat — 롤업 TVL 및 리스크 분석](https://l2beat.com)
> - [Ethereum.org — Layer 2 Rollups](https://ethereum.org/en/layer-2/)
> - [Arbitrum SDK 공식 문서](https://docs.arbitrum.io)
> - [zkSync Era 개발자 문서](https://docs.zksync.io)
> - [Vitalik Buterin — An Incomplete Guide to Rollups](https://vitalik.eth.limo/general/2021/01/05/rollup.html)