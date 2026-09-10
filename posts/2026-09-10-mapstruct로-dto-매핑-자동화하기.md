# MapStruct로 DTO 매핑 자동화하기

## 개요

Spring 기반의 서버 애플리케이션을 개발하다 보면 레이어 간 객체 변환 코드를 끊임없이 작성하게 됩니다. Entity → DTO, DTO → Entity, Request → Command 객체 변환 등 반복적이고 지루한 보일러플레이트 코드가 비즈니스 로직을 오염시키는 상황은 누구나 겪어봤을 것입니다.

```java
// 이런 코드, 익숙하시죠?
public UserDto toDto(User user) {
    UserDto dto = new UserDto();
    dto.setId(user.getId());
    dto.setName(user.getName());
    dto.setEmail(user.getEmail());
    dto.setCreatedAt(user.getCreatedAt());
    // ... 필드가 30개라면?
    return dto;
}
```

**MapStruct**는 이런 문제를 컴파일 타임에 해결해주는 코드 생성 라이브러리입니다. ModelMapper처럼 런타임 리플렉션을 사용하지 않고, 애노테이션 프로세서를 통해 실제 매핑 코드를 생성하기 때문에 **타입 안전성**과 **성능** 두 마리 토끼를 모두 잡을 수 있습니다.

이 글에서는 실무에서 바로 적용 가능한 MapStruct 핵심 개념과 패턴을 다룹니다.

---

## 핵심 개념

### 동작 원리

MapStruct는 `javax.annotation.processing.Processor`를 구현한 애노테이션 프로세서입니다. 컴파일 시점에 `@Mapper` 인터페이스를 분석하여 `target/generated-sources` 아래에 실제 구현 클래스를 생성합니다.

```
@Mapper 인터페이스 정의
       ↓
  컴파일 타임 처리 (APT)
       ↓
 Impl 클래스 자동 생성
       ↓
 Spring Bean으로 등록
```

런타임에 리플렉션이 없으므로 GraalVM Native Image 환경에서도 문제없이 동작하며, 성능 벤치마크에서 ModelMapper 대비 수십 배 빠른 결과를 보여줍니다.

### ModelMapper와의 비교

| 항목 | MapStruct | ModelMapper |
|------|-----------|-------------|
| 매핑 시점 | 컴파일 타임 | 런타임 |
| 타입 안전성 | 높음 (컴파일 오류 감지) | 낮음 (런타임 오류) |
| 성능 | 매우 빠름 | 상대적으로 느림 |
| 디버깅 용이성 | 생성 코드 확인 가능 | 어려움 |
| 학습 곡선 | 중간 | 낮음 |
| Native Image 지원 | 가능 | 제한적 |

---

## 실전 예제

### 의존성 설정

```xml
<!-- Maven -->
<dependency>
    <groupId>org.mapstruct</groupId>
    <artifactId>mapstruct</artifactId>
    <version>1.5.5.Final</version>
</dependency>
<dependency>
    <groupId>org.mapstruct</groupId>
    <artifactId>mapstruct-processor</artifactId>
    <version>1.5.5.Final</version>
    <scope>provided</scope>
</dependency>
```

```groovy
// Gradle (Lombok과 함께 사용 시 순서 중요)
dependencies {
    implementation 'org.mapstruct:mapstruct:1.5.5.Final'
    annotationProcessor 'org.mapstruct:mapstruct-processor:1.5.5.Final'
    
    compileOnly 'org.projectlombok:lombok'
    annotationProcessor 'org.projectlombok:lombok'
    // Lombok은 반드시 MapStruct보다 먼저 선언
    annotationProcessor 'org.projectlombok:lombok-mapstruct-binding:0.2.0'
}
```

### 기본 매핑

```java
// Entity
@Entity
@Getter
@NoArgsConstructor
public class User {
    @Id @GeneratedValue
    private Long id;
    private String name;
    private String email;
    private String phoneNumber;
    private LocalDateTime createdAt;
    
    @ManyToOne(fetch = FetchType.LAZY)
    private Team team;
}

// DTO
@Getter
@Builder
public class UserDto {
    private Long id;
    private String name;
    private String email;
    private String phone;      // 필드명 다름
    private String teamName;   // 중첩 객체 필드
    private String createdAt;  // 타입 다름 (String)
}

// Mapper 인터페이스
@Mapper(componentModel = "spring")
public interface UserMapper {
    
    @Mapping(source = "phoneNumber", target = "phone")
    @Mapping(source = "team.name", target = "teamName")
    @Mapping(target = "createdAt", dateFormat = "yyyy-MM-dd HH:mm:ss")
    UserDto toDto(User user);
    
    @InheritInverseConfiguration
    @Mapping(target = "team", ignore = true) // 역방향 매핑 시 무시
    User toEntity(UserDto dto);
}
```

`componentModel = "spring"`을 지정하면 생성된 구현체가 `@Component`로 등록되어 의존성 주입이 가능합니다.

### 커스텀 변환 로직

단순 필드 매핑 외에 비즈니스 변환 로직이 필요한 경우 `@Named`와 `qualifiedByName`을 활용합니다.

```java
@Mapper(componentModel = "spring")
public interface OrderMapper {
    
    @Mapping(target = "statusLabel", qualifiedByName = "statusToLabel")
    @Mapping(target = "totalPrice", qualifiedByName = "calculateTotalWithTax")
    OrderDto toDto(Order order);
    
    @Named("statusToLabel")
    default String statusToLabel(OrderStatus status) {
        return switch (status) {
            case PENDING -> "결제 대기";
            case PAID -> "결제 완료";
            case SHIPPED -> "배송 중";
            case DELIVERED -> "배송 완료";
            case CANCELLED -> "취소됨";
        };
    }
    
    @Named("calculateTotalWithTax")
    default BigDecimal calculateTotalWithTax(BigDecimal originalPrice) {
        return originalPrice.multiply(BigDecimal.valueOf(1.1))
                            .setScale(0, RoundingMode.HALF_UP);
    }
}
```

### 외부 스프링 빈 주입

매핑 로직에서 Repository나 Service가 필요한 경우 `uses` 속성 또는 추상 클래스 방식을 활용합니다.

```java
// 변환 헬퍼 컴포넌트
@Component
public class UserReferenceMapper {
    
    private final UserRepository userRepository;
    
    public UserReferenceMapper(UserRepository userRepository) {
        this.userRepository = userRepository;
    }
    
    public User idToUser(Long userId) {
        if (userId == null) return null;
        return userRepository.findById(userId)
                .orElseThrow(() -> new EntityNotFoundException("User not found: " + userId));
    }
    
    public Long userToId(User user) {
        return user != null ? user.getId() : null;
    }
}

// Mapper에서 참조
@Mapper(componentModel = "spring", uses = {UserReferenceMapper.class})
public interface PostMapper {
    
    @Mapping(source = "authorId", target = "author") // Long → User 자동 위임
    Post toEntity(PostCreateRequest request);
    
    @Mapping(source = "author", target = "authorId") // User → Long 자동 위임
    PostDto toDto(Post post);
}
```

### 리스트 및 부분 업데이트 매핑

```java
@Mapper(componentModel = "spring")
public interface ProductMapper {
    
    // 리스트 매핑 (자동으로 단건 매핑 메서드를 반복 호출)
    List<ProductDto> toDtoList(List<Product> products);
    
    // 기존 객체를 업데이트하는 패턴 (PATCH 요청 처리에 유용)
    @BeanMapping(nullValuePropertyMappingStrategy = NullValuePropertyMappingStrategy.IGNORE)
    void updateFromRequest(ProductUpdateRequest request, @MappingTarget Product product);
}
```

`NullValuePropertyMappingStrategy.IGNORE`를 지정하면 request의 null 필드는 기존 엔티티 값을 유지합니다. PATCH API 구현 시 매우 유용한 패턴입니다.

```java
// 서비스 레이어에서의 활용
@Transactional
public ProductDto updateProduct(Long id, ProductUpdateRequest request) {
    Product product = productRepository.findById(id)
            .orElseThrow(() -> new EntityNotFoundException("Product not found"));
    
    productMapper.updateFromRequest(request, product);
    // 더티 체킹으로 자동 저장, save() 불필요
    
    return productMapper.toDto(product);
}
```

### 매핑 결과 검증 (생성된 코드 확인)

MapStruct의 강점은 생성된 코드를 직접 확인할 수 있다는 점입니다.

```java
// target/generated-sources/annotations에 생성된 코드 예시
@Component
public class UserMapperImpl implements UserMapper {

    @Override
    public UserDto toDto(User user) {
        if (user == null) {
            return null;
        }

        UserDto.UserDtoBuilder userDto = UserDto.builder();
        userDto.phone(user.getPhoneNumber());
        userDto.teamName(user.getTeam() != null ? user.getTeam().getName() : null);
        userDto.id(user.getId());
        userDto.name(user.getName());
        userDto.email(user.getEmail());
        if (user.getCreatedAt() != null) {
            userDto.createdAt(
                DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss").format(user.getCreatedAt())
            );
        }
        return userDto.build();
    }
}
```

생성 코드가 보이지 않는다면 IDE에서 `Build → Rebuild Project`를 실행하거나 Gradle의 경우 `./gradlew compileJava`를 실행해보세요.

---

## 주의사항 및 트레이드오프

### Lombok과의 순서 문제

MapStruct는 Getter/Setter를 기반으로 매핑 코드를 생성합니다. Lombok이 먼저 실행되어 Getter/Setter를 생성해야 MapStruct가 이를 인식할 수 있습니다. Gradle에서는 `annotationProcessor` 선언 순서, Maven에서는 플러그인 설정으로 순서를 보장해야 합니다.

```xml
<!-- Maven: maven-compiler-plugin 설정 -->
<plugin>
    <groupId>org.apache.maven.plugins</groupId>
    <artifactId>maven-compiler-plugin</artifactId>
    <configuration>
        <annotationProcessorPaths>
            <path>
                <groupId>org.projectlombok</groupId>
                <artifactId>lombok</artifactId>
            </path>
            <!-- Lombok 다음에 MapStruct -->
            <path>
                <groupId>org.mapstruct</groupId>
                <artifactId>mapstruct-processor</artifactId>
            </path>
        </annotationProcessorPaths>
    </configuration>
</plugin>
```

### 순환 참조 주의

양방향 연관관계가 있는 엔티티를 무분별하게 매핑하면 `StackOverflowError`가 발생합니다. 순환 참조가 의심되는 경우 `@Context`와 사이클 감지 맵을 활용하거나, 매핑 깊이를 명시적으로 제한해야 합니다.

```java
// 순환 참조 방어: 필요한 필드만 명시적으로 매핑
@Mapping(target = "comments", ignore = true)  // 역방향 참조 무시
@Mapping(target = "author.posts", ignore = true)
PostDto toDto(Post post);
```

### 지나친 추상화의 위험

MapStruct는 강력하지만, 복잡한 비즈니스 변환 로직을 모두 Mapper 안에 넣으면 책임이 모호해집니다. 다음 기준으로 분리하는 것을 권장합니다.

- **Mapper**: 필드 이름/타입 변환, 단순 포맷 변환
- **Service**: 비즈니스 규칙이 포함된 변환, 다른 도메인과의 연계 조회

### 컴파일 오류 메시지 파악

MapStruct는 매핑 불가능한 필드가 있을 때 컴파일 경고 또는 오류를 발생시킵니다. 기본 설정에서는 경고로 처리되므로 실수가 숨겨질 수 있습니다.

```xml
<!-- 경고를 오류로 격상시켜 안전하게 관리 -->
<plugin>
    <groupId>org.apache.maven.plugins</groupId>
    <artifactId>maven-compiler-plugin</artifactId>
    <configuration>
        <compilerArgs>
            <arg>-Amapstruct.unmappedTargetPolicy=ERROR</arg>
        </compilerArgs>
    </configuration>
</plugin>
```

```groovy
// Gradle
compileJava {
    options.compilerArgs += ['-Amapstruct.unmappedTargetPolicy=ERROR']
}
```

이 설정을 적용하면 매핑되지 않은 타겟 필드가 있을 경우 빌드 자체가 실패하므로, 필드 누락 실수를 사전에 차단할 수 있습니다.

---

## 정리

MapStruct는 단순한 편의 도구가 아니라 **코드 품질과 유지보수성을 높이는 설계 도구**입니다. 핵심 장점을 다시 정리하면:

| 항목 | 내용 |
|------|------|
| **성능** | 컴파일 타임 코드 생성으로 런타임 오버헤드 없음 |
| **안전성** | 타입 불일치, 필드 누락을 컴파일 단계에서 감지 |
| **투명성** | 생성된 코드를 직접 확인하고 디버깅 가능 |
| **확장성** | 커스텀 변환, 외부 Bean 주입 등 유연한 확장 |
| **생태계** | Spring, CDI, Quarkus 등 다양한 프레임워크 지원 |

실무 도입 시에는 `unmappedTargetPolicy=ERROR` 설정으로 안전망을 구성하고, Lombok과의 의존성 순서를 반드시 검토하세요. 그리고 복잡한 비즈니스 로직은 Mapper가 아닌 Service 계층에서 처리한다는 원칙을 지키면, MapStruct는 여러분의 코드베이스에서 묵묵히 수백 줄의 보일러플레이트를 대신해줄 것입니다.
