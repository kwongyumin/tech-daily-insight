# Spring Data JPA Specification으로 동적 쿼리 작성

## 개요

실무 프로젝트에서 검색 기능을 구현하다 보면, 사용자 입력에 따라 WHERE 절의 조건이 동적으로 바뀌는 쿼리를 작성해야 할 때가 많습니다. 예를 들어 상품 검색에서 카테고리, 가격 범위, 키워드, 정렬 조건 등이 선택적으로 적용되어야 하는 경우가 대표적입니다.

이를 해결하는 방법은 여러 가지가 있습니다. `@Query`로 JPQL을 직접 작성하거나, QueryDSL을 도입하거나, MyBatis의 동적 SQL을 활용하는 방법이 있죠. 그중에서도 **Spring Data JPA Specification**은 별도의 라이브러리 추가 없이 JPA 표준 스펙인 `Criteria API`를 기반으로 동적 쿼리를 작성할 수 있는 강력한 방법입니다.

이 포스팅에서는 Specification의 핵심 개념부터 실무에서 즉시 활용 가능한 패턴까지 깊이 있게 다뤄보겠습니다.

---

## 핵심 개념

### JPA Criteria API와 Specification의 관계

`Specification`은 JPA 2.0의 `Criteria API`를 Spring Data JPA가 래핑한 함수형 인터페이스입니다. 내부 구조는 다음과 같이 단 하나의 메서드로 구성됩니다.

```java
@FunctionalInterface
public interface Specification<T> {
    Predicate toPredicate(Root<T> root, CriteriaQuery<?> query, CriteriaBuilder criteriaBuilder);
}
```

- **`Root<T>`**: 쿼리의 FROM 절에 해당하는 엔티티의 루트. 조인이나 경로 표현식을 통해 연관 엔티티에 접근합니다.
- **`CriteriaQuery<?>`**: SELECT, ORDER BY, GROUP BY 등 쿼리 구조 자체를 나타냅니다.
- **`CriteriaBuilder`**: `equal`, `like`, `between`, `and`, `or` 등 Predicate(조건절)를 생성하는 팩토리입니다.

### JpaSpecificationExecutor 활성화

Specification을 사용하려면 Repository가 `JpaSpecificationExecutor`를 상속해야 합니다.

```java
public interface ProductRepository extends JpaRepository<Product, Long>, 
                                           JpaSpecificationExecutor<Product> {
}
```

이것만으로 `findAll(Specification<T> spec)`, `findAll(Specification<T> spec, Pageable pageable)`, `count(Specification<T> spec)` 등의 메서드를 바로 사용할 수 있습니다.

---

## 실전 예제

### 도메인 모델 설정

실무에서 흔히 볼 수 있는 상품(Product)과 카테고리(Category) 관계를 예시로 사용합니다.

```java
@Entity
@Table(name = "products")
@Getter
@NoArgsConstructor
public class Product {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String name;
    private BigDecimal price;
    private Integer stockQuantity;
    private LocalDateTime createdAt;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "category_id")
    private Category category;
}

@Entity
@Table(name = "categories")
@Getter
@NoArgsConstructor
public class Category {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String name;
}
```

### 검색 조건 DTO

```java
@Getter
@Builder
public class ProductSearchCondition {
    private String name;
    private String categoryName;
    private BigDecimal minPrice;
    private BigDecimal maxPrice;
    private Boolean inStock;
    private LocalDateTime createdFrom;
    private LocalDateTime createdTo;
}
```

### Specification 클래스 작성

Specification 로직은 별도의 클래스에 **정적 팩토리 메서드** 형태로 분리하는 것이 유지보수에 유리합니다.

```java
public class ProductSpecification {

    public static Specification<Product> nameContains(String name) {
        return (root, query, cb) -> {
            if (!StringUtils.hasText(name)) return null;
            return cb.like(cb.lower(root.get("name")), "%" + name.toLowerCase() + "%");
        };
    }

    public static Specification<Product> categoryNameEquals(String categoryName) {
        return (root, query, cb) -> {
            if (!StringUtils.hasText(categoryName)) return null;
            // 연관 엔티티 조인
            Join<Product, Category> categoryJoin = root.join("category", JoinType.LEFT);
            return cb.equal(categoryJoin.get("name"), categoryName);
        };
    }

    public static Specification<Product> priceBetween(BigDecimal min, BigDecimal max) {
        return (root, query, cb) -> {
            if (min == null && max == null) return null;
            if (min == null) return cb.lessThanOrEqualTo(root.get("price"), max);
            if (max == null) return cb.greaterThanOrEqualTo(root.get("price"), min);
            return cb.between(root.get("price"), min, max);
        };
    }

    public static Specification<Product> inStock(Boolean inStock) {
        return (root, query, cb) -> {
            if (inStock == null || !inStock) return null;
            return cb.greaterThan(root.get("stockQuantity"), 0);
        };
    }

    public static Specification<Product> createdBetween(LocalDateTime from, LocalDateTime to) {
        return (root, query, cb) -> {
            if (from == null && to == null) return null;
            if (from == null) return cb.lessThanOrEqualTo(root.get("createdAt"), to);
            if (to == null) return cb.greaterThanOrEqualTo(root.get("createdAt"), from);
            return cb.between(root.get("createdAt"), from, to);
        };
    }
}
```

### Specification 조합 및 서비스 레이어

`Specification.where()`와 `.and()`, `.or()` 메서드를 체이닝하여 동적으로 조건을 조합합니다.

```java
@Service
@RequiredArgsConstructor
public class ProductService {

    private final ProductRepository productRepository;

    public Page<ProductResponse> searchProducts(ProductSearchCondition condition, Pageable pageable) {
        Specification<Product> spec = Specification
            .where(ProductSpecification.nameContains(condition.getName()))
            .and(ProductSpecification.categoryNameEquals(condition.getCategoryName()))
            .and(ProductSpecification.priceBetween(condition.getMinPrice(), condition.getMaxPrice()))
            .and(ProductSpecification.inStock(condition.getInStock()))
            .and(ProductSpecification.createdBetween(condition.getCreatedFrom(), condition.getCreatedTo()));

        return productRepository.findAll(spec, pageable)
                .map(ProductResponse::from);
    }
}
```

`null`을 반환하는 Specification은 Spring Data JPA가 자동으로 무시하기 때문에 별도의 null 체크 분기 없이 깔끔하게 조합할 수 있습니다.

### 컨트롤러 레이어

```java
@RestController
@RequestMapping("/api/products")
@RequiredArgsConstructor
public class ProductController {

    private final ProductService productService;

    @GetMapping
    public ResponseEntity<Page<ProductResponse>> searchProducts(
            @RequestParam(required = false) String name,
            @RequestParam(required = false) String categoryName,
            @RequestParam(required = false) BigDecimal minPrice,
            @RequestParam(required = false) BigDecimal maxPrice,
            @RequestParam(required = false) Boolean inStock,
            @RequestParam(required = false) @DateTimeFormat(iso = ISO.DATE_TIME) LocalDateTime createdFrom,
            @RequestParam(required = false) @DateTimeFormat(iso = ISO.DATE_TIME) LocalDateTime createdTo,
            Pageable pageable) {

        ProductSearchCondition condition = ProductSearchCondition.builder()
                .name(name)
                .categoryName(categoryName)
                .minPrice(minPrice)
                .maxPrice(maxPrice)
                .inStock(inStock)
                .createdFrom(createdFrom)
                .createdTo(createdTo)
                .build();

        return ResponseEntity.ok(productService.searchProducts(condition, pageable));
    }
}
```

### count 쿼리 최적화 (페이징 성능 개선)

페이징 처리 시 카운트 쿼리에서도 불필요한 조인이 발생하는 것을 방지하려면, `CriteriaQuery`의 `isCountQuery()` 여부를 체크하는 패턴을 활용할 수 있습니다.

```java
public static Specification<Product> categoryNameEquals(String categoryName) {
    return (root, query, cb) -> {
        if (!StringUtils.hasText(categoryName)) return null;

        // count 쿼리 시 fetch join 제외
        if (Long.class != query.getResultType()) {
            root.fetch("category", JoinType.LEFT);
        }

        Join<Product, Category> categoryJoin = root.join("category", JoinType.LEFT);
        return cb.equal(categoryJoin.get("name"), categoryName);
    };
}
```

---

## 주의사항 및 트레이드오프

### 1. N+1 문제

`FetchType.LAZY`로 설정된 연관 관계를 Specification 내에서 `join`만 하고 `fetch`를 하지 않으면 N+1이 발생할 수 있습니다. 위 예시처럼 일반 `findAll` 쿼리에서는 `root.fetch()`를 적절히 활용하고, 카운트 쿼리에서는 제거하는 패턴을 적용하세요.

### 2. 타입 안전성 문제

현재 예시처럼 문자열로 필드명을 지정하면(`root.get("name")`) 컴파일 타임에 오류를 잡을 수 없습니다. **JPA Static Metamodel**을 활용하면 타입 안전한 방식으로 작성할 수 있습니다.

```java
// Metamodel 사용 시 (빌드 도구로 자동 생성)
// root.get("name") → root.get(Product_.name)
cb.like(cb.lower(root.get(Product_.name)), "%" + name.toLowerCase() + "%");
```

Metamodel 클래스는 Hibernate 의존성만 있으면 `hibernate-jpamodelgen` 플러그인으로 자동 생성됩니다.

### 3. 복잡한 쿼리에서의 한계

서브쿼리, CASE WHEN, 복잡한 GROUP BY/HAVING이 필요한 경우 Criteria API의 코드가 매우 장황해집니다. 이 경우 **QueryDSL**이 훨씬 직관적인 대안입니다.

| 기준 | Specification | QueryDSL |
|------|--------------|----------|
| 추가 의존성 | 불필요 | 필요 (querydsl-jpa) |
| 타입 안전성 | Metamodel 필요 | 기본 제공 |
| 코드 가독성 | 복잡해질 수 있음 | 높음 |
| 러닝 커브 | 낮음 | 중간 |
| 서브쿼리/복잡 조인 | 어려움 | 용이 |

### 4. 동일 엔티티 중복 조인 문제

여러 Specification을 조합할 때 같은 엔티티를 중복 조인하면 카르테시안 곱이 발생하거나 예외가 발생할 수 있습니다. 조인이 필요한 경우 `root.getJoins()`로 이미 존재하는 조인을 재사용하는 방어 로직을 추가하는 것이 안전합니다.

```java
private static Join<Product, Category> getOrCreateCategoryJoin(Root<Product> root) {
    return root.getJoins().stream()
            .filter(j -> j.getAttribute().getName().equals("category"))
            .map(j -> (Join<Product, Category>) j)
            .findFirst()
            .orElseGet(() -> root.join("category", JoinType.LEFT));
}
```

---

## 정리

Spring Data JPA Specification은 **추가 의존성 없이** JPA 표준 방식으로 동적 쿼리를 구성할 수 있는 실용적인 도구입니다. 핵심 포인트를 정리하면 다음과 같습니다.

- **정적 팩토리 메서드 패턴**으로 Specification을 분리하면 재사용성과 테스트 용이성이 높아집니다.
- `null` 반환 Specification은 자동으로 무시되므로 조건 분기 코드를 최소화할 수 있습니다.
- 페이징 시 **count 쿼리에서 fetch join을 제거**하는 패턴은 성능상 반드시 챙겨야 할 부분입니다.
- 타입 안전성이 중요하다면 **JPA Static Metamodel**을 활용하세요.
- 서브쿼리나 복잡한 집계가 필요하다면 QueryDSL로의 전환을 고려할 때입니다.

단순 검색 필터 수준의 동적 쿼리라면 Specification만으로도 충분히 깔끔하고 유지보수 가능한 코드를 작성할 수 있습니다. 팀의 기술 스택과 쿼리의 복잡도에 따라 적절한 도구를 선택하는 것이 중요합니다.