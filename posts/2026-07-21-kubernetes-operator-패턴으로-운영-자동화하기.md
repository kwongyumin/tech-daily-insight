# Kubernetes Operator 패턴으로 운영 자동화하기

## 개요

Kubernetes를 운영하다 보면 단순한 Pod 배포를 넘어 복잡한 상태를 관리해야 하는 순간이 찾아온다. 데이터베이스 클러스터를 구성하거나, 자동 백업을 수행하거나, 장애 발생 시 자동으로 복구하는 일련의 작업들은 `kubectl apply` 한 번으로 끝나지 않는다. 이런 **도메인 특화된 운영 로직**을 코드로 캡슐화하여 Kubernetes 위에서 자동화하는 것이 바로 **Operator 패턴**이다.

Operator는 Kubernetes의 **Custom Resource Definition(CRD)**와 **Controller**를 결합한 아키텍처다. 쉽게 말하면, 숙련된 운영자(Operator)가 수행할 작업을 코드로 구현해 클러스터 안에 상주시키는 개념이다. Prometheus Operator, cert-manager, Strimzi(Kafka Operator) 등이 대표적인 실전 사례다.

이 글에서는 Operator 패턴의 핵심 개념부터 Go 언어와 controller-runtime을 이용한 실전 구현까지 다룬다.

---

## 핵심 개념

### Control Loop와 Reconciliation

Kubernetes의 모든 컨트롤러는 다음 루프를 기반으로 동작한다.

```
Observe(관찰) → Diff(비교) → Act(행동)
```

이를 **Reconcile Loop**라고 부른다. 현재 상태(Current State)와 원하는 상태(Desired State)를 비교하고, 차이가 있다면 이를 수렴(converge)시키는 방향으로 동작한다. Operator도 동일한 원리를 따른다.

### CRD (Custom Resource Definition)

CRD는 Kubernetes API를 확장하는 방법이다. 기본 제공되는 `Deployment`, `Service` 외에 직접 정의한 리소스 타입을 추가할 수 있다.

```yaml
# crd.yaml
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: myapps.example.com
spec:
  group: example.com
  versions:
    - name: v1alpha1
      served: true
      storage: true
      schema:
        openAPIV3Schema:
          type: object
          properties:
            spec:
              type: object
              properties:
                replicas:
                  type: integer
                  minimum: 1
                image:
                  type: string
                enableAutoScaling:
                  type: boolean
            status:
              type: object
              properties:
                availableReplicas:
                  type: integer
                phase:
                  type: string
  scope: Namespaced
  names:
    plural: myapps
    singular: myapp
    kind: MyApp
```

### Custom Resource (CR)

CRD를 적용하면 이제 `MyApp` 타입의 리소스를 선언할 수 있다.

```yaml
# myapp-sample.yaml
apiVersion: example.com/v1alpha1
kind: MyApp
metadata:
  name: my-application
  namespace: default
spec:
  replicas: 3
  image: nginx:1.25
  enableAutoScaling: true
```

이 선언을 보고 **Controller**가 적절한 Deployment, HPA 등을 생성하고 관리하는 것이 Operator의 핵심이다.

---

## 실전 예제: Go + controller-runtime으로 Operator 구현

### 프로젝트 셋업

[Operator SDK](https://sdk.operatorframework.io/)나 [Kubebuilder](https://book.kubebuilder.io/)를 사용하면 보일러플레이트를 자동 생성할 수 있다.

```bash
# kubebuilder 설치 후 프로젝트 초기화
kubebuilder init --domain example.com --repo github.com/myorg/myapp-operator

# API (CRD + Controller) 생성
kubebuilder create api --group apps --version v1alpha1 --kind MyApp
```

### API 타입 정의

```go
// api/v1alpha1/myapp_types.go
package v1alpha1

import (
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

type MyAppSpec struct {
    // +kubebuilder:validation:Minimum=1
    Replicas int32 `json:"replicas"`
    Image    string `json:"image"`
    // +optional
    EnableAutoScaling bool `json:"enableAutoScaling,omitempty"`
}

type MyAppStatus struct {
    AvailableReplicas int32  `json:"availableReplicas,omitempty"`
    Phase             string `json:"phase,omitempty"`
    // Conditions는 표준 Kubernetes 패턴을 따름
    Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Replicas",type="integer",JSONPath=".spec.replicas"
// +kubebuilder:printcolumn:name="Phase",type="string",JSONPath=".status.phase"
type MyApp struct {
    metav1.TypeMeta   `json:",inline"`
    metav1.ObjectMeta `json:"metadata,omitempty"`

    Spec   MyAppSpec   `json:"spec,omitempty"`
    Status MyAppStatus `json:"status,omitempty"`
}
```

### Reconciler 구현

핵심이 되는 `Reconcile` 함수다. 여기에 실제 운영 로직이 담긴다.

```go
// internal/controller/myapp_controller.go
package controller

import (
    "context"
    "fmt"

    appsv1 "k8s.io/api/apps/v1"
    corev1 "k8s.io/api/core/v1"
    "k8s.io/apimachinery/pkg/api/errors"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    "k8s.io/apimachinery/pkg/runtime"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/client"
    "sigs.k8s.io/controller-runtime/pkg/log"

    examplev1alpha1 "github.com/myorg/myapp-operator/api/v1alpha1"
)

type MyAppReconciler struct {
    client.Client
    Scheme *runtime.Scheme
}

func (r *MyAppReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
    logger := log.FromContext(ctx)

    // 1. CR 조회
    myApp := &examplev1alpha1.MyApp{}
    if err := r.Get(ctx, req.NamespacedName, myApp); err != nil {
        if errors.IsNotFound(err) {
            // 리소스가 삭제된 경우 - 정리 작업은 Finalizer로 처리
            return ctrl.Result{}, nil
        }
        return ctrl.Result{}, err
    }

    // 2. Deployment 조회 또는 생성
    deployment := &appsv1.Deployment{}
    err := r.Get(ctx, req.NamespacedName, deployment)
    if errors.IsNotFound(err) {
        // Deployment가 없으면 생성
        newDeploy := r.buildDeployment(myApp)
        if err := ctrl.SetControllerReference(myApp, newDeploy, r.Scheme); err != nil {
            return ctrl.Result{}, err
        }
        logger.Info("Creating Deployment", "name", newDeploy.Name)
        if err := r.Create(ctx, newDeploy); err != nil {
            return ctrl.Result{}, err
        }
        // Status 업데이트
        myApp.Status.Phase = "Creating"
        r.Status().Update(ctx, myApp)
        return ctrl.Result{Requeue: true}, nil
    } else if err != nil {
        return ctrl.Result{}, err
    }

    // 3. Desired State와 Current State 비교 후 동기화
    if *deployment.Spec.Replicas != myApp.Spec.Replicas {
        logger.Info("Updating replicas", "from", *deployment.Spec.Replicas, "to", myApp.Spec.Replicas)
        deployment.Spec.Replicas = &myApp.Spec.Replicas
        if err := r.Update(ctx, deployment); err != nil {
            return ctrl.Result{}, err
        }
    }

    // 4. Status 업데이트
    myApp.Status.AvailableReplicas = deployment.Status.AvailableReplicas
    if deployment.Status.AvailableReplicas == myApp.Spec.Replicas {
        myApp.Status.Phase = "Running"
    } else {
        myApp.Status.Phase = "Progressing"
    }

    if err := r.Status().Update(ctx, myApp); err != nil {
        return ctrl.Result{}, err
    }

    return ctrl.Result{}, nil
}

func (r *MyAppReconciler) buildDeployment(myApp *examplev1alpha1.MyApp) *appsv1.Deployment {
    labels := map[string]string{"app": myApp.Name}
    return &appsv1.Deployment{
        ObjectMeta: metav1.ObjectMeta{
            Name:      myApp.Name,
            Namespace: myApp.Namespace,
        },
        Spec: appsv1.DeploymentSpec{
            Replicas: &myApp.Spec.Replicas,
            Selector: &metav1.LabelSelector{MatchLabels: labels},
            Template: corev1.PodTemplateSpec{
                ObjectMeta: metav1.ObjectMeta{Labels: labels},
                Spec: corev1.PodSpec{
                    Containers: []corev1.Container{
                        {
                            Name:  myApp.Name,
                            Image: myApp.Spec.Image,
                        },
                    },
                },
            },
        },
    }
}

func (r *MyAppReconciler) SetupWithManager(mgr ctrl.Manager) error {
    return ctrl.NewControllerManagedBy(mgr).
        For(&examplev1alpha1.MyApp{}).
        // 소유한 Deployment 변경도 감지
        Owns(&appsv1.Deployment{}).
        Complete(r)
}
```

### Finalizer로 정리 작업 처리

삭제 시 외부 리소스(DB, DNS 등)를 정리해야 한다면 Finalizer를 사용한다.

```go
const myAppFinalizer = "example.com/finalizer"

func (r *MyAppReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
    myApp := &examplev1alpha1.MyApp{}
    if err := r.Get(ctx, req.NamespacedName, myApp); err != nil {
        return ctrl.Result{}, client.IgnoreNotFound(err)
    }

    // 삭제 타임스탬프가 있으면 정리 작업 수행
    if !myApp.DeletionTimestamp.IsZero() {
        if containsString(myApp.Finalizers, myAppFinalizer) {
            // 외부 리소스 정리
            if err := r.cleanupExternalResources(ctx, myApp); err != nil {
                return ctrl.Result{}, err
            }
            // Finalizer 제거 → 실제 삭제 진행
            myApp.Finalizers = removeString(myApp.Finalizers, myAppFinalizer)
            return ctrl.Result{}, r.Update(ctx, myApp)
        }
        return ctrl.Result{}, nil
    }

    // Finalizer 등록
    if !containsString(myApp.Finalizers, myAppFinalizer) {
        myApp.Finalizers = append(myApp.Finalizers, myAppFinalizer)
        return ctrl.Result{}, r.Update(ctx, myApp)
    }

    // ... 일반 Reconcile 로직
    return ctrl.Result{}, nil
}
```

---

## 주의사항 및 트레이드오프

### 1. Reconcile 함수는 멱등(Idempotent)해야 한다

Reconcile은 언제든 재호출될 수 있다. Pod 재시작, 네트워크 오류, 리더 선출 이후 재실행 등 다양한 이유로 동일한 이벤트가 여러 번 처리될 수 있다. 리소스를 `Create`하기 전에 반드시 `Get`으로 존재 여부를 확인하고, 없을 때만 생성하는 패턴을 지켜야 한다.

### 2. Status와 Spec의 분리

`Spec`은 사용자가 선언하는 **Desired State**, `Status`는 컨트롤러가 기록하는 **Current State**다. Status를 수정할 때는 반드시 `r.Status().Update()`를 사용해야 하며, 일반 `r.Update()`로 Status를 변경하면 Spec 서브리소스가 활성화된 경우 변경이 무시된다.

### 3. Requeue 전략을 신중하게

```go
// 즉시 재시도
return ctrl.Result{Requeue: true}, nil

// 30초 후 재시도 (주기적 체크에 유용)
return ctrl.Result{RequeueAfter: 30 * time.Second}, nil

// 에러 발생 시 (지수 백오프 자동 적용)
return ctrl.Result{}, err
```

불필요한 `Requeue: true` 남발은 API 서버 부하를 높이고 레이트 리밋 문제를 유발한다. 변경 사항이 없을 때는 빈 `Result`를 반환해 Watch 이벤트에만 반응하도록 한다.

### 4. RBAC 권한 최소화

`+kubebuilder:rbac` 마커를 이용해 컨트롤러가 필요한 리소스에만 접근하도록 제한한다.

```go
// +kubebuilder:rbac:groups=apps.example.com,resources=myapps,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=apps.example.com,resources=myapps/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;create;update;patch;delete
```

### 5. 복잡도와 유지보수 비용

Operator는 강력하지만 그만큼 복잡도도 높다. 단순히 Helm chart나 ArgoCD로 해결 가능한 문제라면 Operator를 도입할 필요가 없다. **상태 기반 자동화**, **도메인 특화 운영 로직**, **다단계 라이프사이클 관리**가 필요할 때 비로소 Operator의 가치가 발휘된다.

---

## 정리

| 구성 요소 | 역할 |
|-----------|------|
| **CRD** | 새로운 API 타입 정의 |
| **CR** | 사용자가 선언하는 원하는 상태 |
| **Controller** | Reconcile 루프를 통해 상태 수렴 |
| **Finalizer** | 삭제 시 외부 리소스 정리 |
| **Status** | 컨트롤러가 기록하는 현재 상태 |

Kubernetes Operator 패턴은 단순한 배포 자동화를 넘어 **운영 노하우를 코드로 표현**하는 방식이다. 데이터베이스 페일오버, 인증서 갱신, 카나리 배포 자동화 등 다양한 운영 업무를 Operator로 구현하면 인적 오류를 줄이고 24시간 안정적인 운영을 실현할 수 있다.

시작점으로는 Kubebuilder 공식 튜토리얼을 따라가며 간단한 Operator를 직접 만들어 보는 것을 강력히 추천한다. 처음에는 보일러플레이트가 많아 진입 장벽처럼 느껴지지만, 한 번 구조를 이해하고 나면 팀의 운영 자동화 수준을 한 단계 끌어올릴 수 있는 강력한 도구가 될 것이다.