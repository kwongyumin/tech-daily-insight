# WebAssembly(WASM) 서버사이드 실행 가능성과 미래

## 개요

WebAssembly(WASM)는 처음에는 브라우저에서 고성능 애플리케이션을 실행하기 위한 기술로 등장했습니다. C/C++, Rust, Go 등의 언어를 컴파일하여 브라우저에서 네이티브에 가까운 성능을 낼 수 있다는 점이 주목받았죠. 그런데 최근 몇 년 사이, WASM은 브라우저를 벗어나 **서버사이드 런타임 환경**으로 빠르게 확산되고 있습니다.

Docker의 공동 창업자 Solomon Hykes가 2019년에 남긴 유명한 말이 있습니다.

> "WASM+WASI가 2008년에 존재했다면, Docker를 만들지 않았을 것이다."

이 발언은 WASM이 단순한 브라우저 기술을 넘어서 **컨테이너를 대체할 수 있는 런타임 기술**로 진화할 가능성을 시사합니다. 이 글에서는 서버사이드 WASM의 핵심 개념부터 실전 예제, 그리고 실무에서 고려해야 할 트레이드오프까지 깊이 있게 다뤄보겠습니다.

---

## 핵심 개념

### WASI: 브라우저 밖의 WebAssembly

브라우저 환경에서 WASM은 JavaScript와 브라우저 API에 의존합니다. 서버에서 실행하려면 파일 시스템, 네트워크 소켓, 환경 변수 등 OS 레벨 리소스에 접근해야 합니다. 이를 위해 등장한 것이 **WASI(WebAssembly System Interface)**입니다.

WASI는 POSIX 유사 인터페이스를 통해 WASM 모듈이 OS 리소스에 접근할 수 있도록 합니다. 단, 기본적으로 **Capability-based 보안 모델**을 채택하여, 명시적으로 허용된 리소스에만 접근할 수 있습니다.

```
[WASM Module]
     ↓
[WASI Interface]
     ↓
[WASM Runtime (Wasmtime / WasmEdge / Wasmer)]
     ↓
[Host OS (Linux / macOS / Windows)]
```

### 주요 서버사이드 WASM 런타임

| 런타임 | 주요 특징 | 사용 사례 |
|--------|-----------|-----------|
| **Wasmtime** | Bytecode Alliance 주도, Rust 구현 | 서버 일반 목적 |
| **WasmEdge** | CNCF 프로젝트, 클라우드 네이티브 | Serverless, Edge |
| **Wasmer** | 다양한 언어 임베딩 지원 | SDK, CLI |
| **wazero** | Go 순수 구현, 의존성 없음 | Go 애플리케이션 임베딩 |

### WASM Component Model

2023~2024년에 급부상한 **Component Model**은 WASM 모듈 간의 상호운용성을 표준화합니다. 서로 다른 언어로 작성된 WASM 모듈이 타입 안전하게 통신할 수 있게 해주며, 이는 마이크로서비스 아키텍처의 새로운 패러다임을 열어줍니다.

```wit
// WIT (WASM Interface Types) 예시
package example:calculator;

interface math {
  add: func(a: f64, b: f64) -> f64;
  multiply: func(a: f64, b: f64) -> f64;
}

world calculator {
  export math;
}
```

---

## 실전 예제

### 예제 1: Rust로 WASM 모듈 작성 후 서버에서 실행

먼저 Rust로 간단한 WASM 모듈을 작성하고, Java/Spring 서버에서 이를 실행하는 예제입니다.

**Rust WASM 모듈 작성 (wasm-module/src/lib.rs)**

```rust
use std::io::{self, Read};

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    
    // JSON 파싱 및 변환 로직 (예시)
    let trimmed = input.trim();
    let word_count = trimmed.split_whitespace().count();
    
    println!("{{\"word_count\": {}, \"char_count\": {}}}", 
        word_count, 
        trimmed.len()
    );
}
```

```bash
# WASI 타겟으로 컴파일
rustup target add wasm32-wasi
cargo build --target wasm32-wasi --release
# 결과: target/wasm32-wasi/release/wasm_module.wasm
```

**Spring Boot에서 Wasmtime Java 바인딩으로 실행**

```xml
<!-- pom.xml -->
<dependency>
    <groupId>io.github.kawamuray.wasmtime</groupId>
    <artifactId>wasmtime-java</artifactId>
    <version>0.18.0</version>
</dependency>
```

```java
@Service
public class WasmExecutorService {

    private final Path wasmModulePath;

    public WasmExecutorService(@Value("${wasm.module.path}") String modulePath) {
        this.wasmModulePath = Paths.get(modulePath);
    }

    public String executeTextAnalysis(String input) {
        try (Engine engine = Engine.newDefault();
             Store<Void> store = Store.withoutData(engine);
             Module module = Module.fromFile(engine, wasmModulePath.toString())) {

            WasiCtx wasiCtx = new WasiCtxBuilder()
                .inheritStdout()
                .inheritStderr()
                .stdin(new ByteArrayInputStream(input.getBytes()))
                .build();

            Linker linker = new Linker(engine);
            WasiCtx.addToLinker(linker);
            linker.module(store, "", module);

            Func mainFunc = linker.get(store, "", "_start")
                .orElseThrow(() -> new RuntimeException("_start not found"))
                .func();

            mainFunc.call(store);
            
            return captureOutput(store);
            
        } catch (Exception e) {
            throw new WasmExecutionException("WASM 실행 실패", e);
        }
    }
}
```

### 예제 2: WasmEdge로 Edge Function 구현

**JavaScript/TypeScript 기반 Edge Function (Fastly Compute@Edge 스타일)**

```rust
// Rust로 HTTP 핸들러 작성
use wasi_http::*;

#[no_mangle]
pub extern "C" fn handle_request() {
    let request = incoming_request();
    let path = request.path();
    
    let (status, body) = match path.as_str() {
        "/health" => (200, r#"{"status":"ok"}"#.to_string()),
        "/transform" => {
            let body = request.body_text().unwrap_or_default();
            let transformed = transform_data(&body);
            (200, transformed)
        },
        _ => (404, r#"{"error":"not found"}"#.to_string()),
    };
    
    send_response(status, &[
        ("Content-Type", "application/json"),
    ], body.as_bytes());
}

fn transform_data(input: &str) -> String {
    // 데이터 변환 로직
    format!(r#"{{"transformed": "{}", "timestamp": {}}}"#,
        input.to_uppercase(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs()
    )
}
```

### 예제 3: wazero를 활용한 Go 서버 플러그인 시스템

```go
package main

import (
    "context"
    "fmt"
    "os"

    "github.com/tetratelabs/wazero"
    "github.com/tetratelabs/wazero/imports/wasi_snapshot_preview1"
)

type PluginManager struct {
    runtime wazero.Runtime
    plugins map[string]wazero.CompiledModule
}

func NewPluginManager(ctx context.Context) (*PluginManager, error) {
    r := wazero.NewRuntime(ctx)
    wasi_snapshot_preview1.MustInstantiate(ctx, r)
    
    return &PluginManager{
        runtime: r,
        plugins: make(map[string]wazero.CompiledModule),
    }, nil
}

func (pm *PluginManager) LoadPlugin(ctx context.Context, name, wasmPath string) error {
    wasmBytes, err := os.ReadFile(wasmPath)
    if err != nil {
        return fmt.Errorf("플러그인 파일 읽기 실패: %w", err)
    }
    
    compiled, err := pm.runtime.CompileModule(ctx, wasmBytes)
    if err != nil {
        return fmt.Errorf("WASM 컴파일 실패: %w", err)
    }
    
    pm.plugins[name] = compiled
    return nil
}

func (pm *PluginManager) ExecutePlugin(ctx context.Context, name string, input []byte) ([]byte, error) {
    compiled, ok := pm.plugins[name]
    if !ok {
        return nil, fmt.Errorf("플러그인 '%s'를 찾을 수 없음", name)
    }
    
    // 각 실행마다 새로운 인스턴스 생성 (격리 보장)
    mod, err := pm.runtime.InstantiateModule(ctx, compiled,
        wazero.NewModuleConfig().
            WithStdin(bytes.NewReader(input)).
            WithStdout(os.Stdout).
            WithArgs("plugin"))
    if err != nil {
        return nil, fmt.Errorf("모듈 인스턴스화 실패: %w", err)
    }
    defer mod.Close(ctx)
    
    return captureModuleOutput(mod), nil
}
```

---

## 주의사항 및 트레이드오프

### 성능 특성 이해

WASM은 "네이티브에 가까운 성능"이라고 홍보되지만, 실제로는 **상황에 따라 다릅니다**.

```
콜드 스타트 시간 비교 (참고용):
- 일반 프로세스 실행:  ~수십 ms
- Docker 컨테이너:     ~100ms ~ 수 초
- WASM 모듈 (Wasmtime): ~1~5ms
- WASM (사전 컴파일):  ~수십 μs
```

계산 집약적 작업에서는 네이티브 대비 5~30% 성능 저하가 있을 수 있으며, I/O 집약적 작업에서는 WASI 오버헤드가 더 크게 작용할 수 있습니다.

### 현재의 한계점

**1. WASI 성숙도 문제**

WASI Preview 2가 발표되었지만, 전체 POSIX 호환성은 아직 완전하지 않습니다. 특히 멀티스레딩과 네트워크 소켓 지원은 여전히 발전 중입니다.

```
현재 WASI 지원 현황 (2024 기준):
✅ 파일 시스템 접근 (제한적)
✅ 표준 입출력
✅ 환경 변수
⚠️  소켓 네트워킹 (WASI Preview 2에서 일부 지원)
⚠️  멀티스레딩 (실험적)
❌ 완전한 POSIX 호환성
```

**2. 언어 지원 격차**

모든 언어가 동등하게 WASM을 지원하지는 않습니다. Rust와 C/C++는 최고 수준의 지원을 제공하지만, Java나 Python은 GC 및 런타임 포함으로 인해 바이너리 크기와 성능 면에서 불리합니다.

**3. 생태계 분열**

Wasmtime, WasmEdge, Wasmer 등 각 런타임의 WASI 구현에 미묘한 차이가 있어 이식성이 완전하지 않습니다.

### 보안 고려사항

WASM의 샌드박스 모델은 강력하지만 완벽하지 않습니다.

```rust
// 잘못된 예: 과도한 권한 부여
let wasi = WasiCtxBuilder::new()
    .inherit_stdio()
    .inherit_env()       // ❌ 모든 환경 변수 노출
    .preopened_dir(Dir::from_std_file(
        File::open("/").unwrap()),  // ❌ 루트 접근 허용
        "/"
    )
    .build();

// 올바른 예: 최소 권한 원칙
let wasi = WasiCtxBuilder::new()
    .inherit_stdio()
    .env("APP_ENV", "production")  // ✅ 필요한 환경 변수만
    .preopened_dir(Dir::from_std_file(
        File::open("/app/data").unwrap()),  // ✅ 특정 디렉토리만
        "/data"
    )
    .build();
```

### 언제 WASM을 선택해야 하는가

```
✅ WASM이 적합한 경우:
- 신뢰할 수 없는 서드파티 코드 실행 (플러그인 시스템)
- 멀티 테넌트 환경에서 격리가 중요한 경우
- Edge 컴퓨팅 / CDN 레벨 로직
- 초저지연 콜드 스타트가 필요한 Serverless
- 다중 언어 환경에서 공통 런타임이 필요한 경우

❌ WASM이 부적합한 경우:
- 완전한 OS 기능이 필요한 복잡한 애플리케이션
- POSIX 완전 호환이 필수인 레거시 코드
- Java EE / Spring 생태계 전체를 서버사이드 WASM으로 이전하려는 시도
- 팀의 WASM 학습 비용이 비즈니스 가치보다 클 때
```

---

## 정리

서버사이드 WebAssembly는 분명히 단순한 유행이 아닙니다. WASI의 발전, Component Model의 표준화, 주요 클라우드 플랫폼(Cloudflare Workers, Fastly Compute@Edge, Fermyon Spin)의 채택은 이 기술이 실용적 단계에 접어들었음을 보여줍니다.

그러나 "모든 것을 WASM으로 대체"하려는 접근은 경계해야 합니다. 현재 가장 실용적인 도입 시나리오는 **플러그인 시스템**, **Edge Function**, **신뢰할 수 없는 코드의 안전한 실행** 영역입니다.

백엔드 개발자 관점에서 지금 당장 준비해야 할 것들은 다음과 같습니다.

1. **Rust 또는 Go 기반 WASM 모듈 작성 경험** 쌓기
2. **Wasmtime 또는 wazero** 런타임 임베딩 실험
3. **WIT(WASM Interface Types)** 와 Component Model 개념 숙지
4. Fermyon Spin, Cloudflare Workers 등 **WASM 네이티브 플랫폼** 실습

Solomon Hykes의 말처럼, WASM이 컨테이너를 완전히 대체할지는 미지수입니다. 하지만 특정 영역에서 컨테이너보다 경량하고 안전한 실행 단위로서 **컨테이너와 공존하며 생태계를 확장**할 가능성은 매우 높습니다. 지금이 서버사이드 WASM을 탐구하기 가장 좋은 시기입니다.

---

*참고 자료*
- [Bytecode Alliance - WASI](https://bytecodealliance.org/)
- [WebAssembly Component Model Spec](https://github.com/WebAssembly/component-model)
- [Fermyon Spin Documentation](https://developer.fermyon.com/spin)
- [wazero - Go WASM Runtime](https://wazero.io/)