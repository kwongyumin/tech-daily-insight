# Terraform으로 클라우드 인프라 코드화 (IaC)

## 개요

클라우드 인프라를 콘솔에서 수동으로 클릭해 생성하던 시대는 지나갔다. 인프라가 복잡해지고 팀이 커질수록, "누가 언제 어떤 리소스를 만들었는가"를 추적하지 못하는 문제가 반드시 터진다. 프로덕션 환경과 스테이징 환경이 미묘하게 다르거나, 온보딩한 신입이 실수로 보안 그룹을 잘못 설정하거나, 장애 복구 시 인프라를 재현하지 못하는 상황 — 이 모든 문제의 근원은 **인프라가 코드로 관리되지 않는다는 것**이다.

**Infrastructure as Code(IaC)** 는 이러한 문제를 해결하기 위한 패러다임이고, 그 중심에 **Terraform**이 있다. HashiCorp가 만든 Terraform은 선언형(Declarative) 방식으로 클라우드 리소스를 정의하고, AWS, GCP, Azure 등 멀티 클라우드 환경을 단일 도구로 관리할 수 있게 해준다.

이 글에서는 실무에서 Terraform을 도입할 때 반드시 알아야 할 핵심 개념과, 실제 AWS 인프라를 코드로 구성하는 예제를 다룬다.

---

## 핵심 개념

### Provider & Resource

Terraform은 **Provider**를 통해 특정 클라우드나 서비스와 통신한다. Provider는 플러그인 형태로 동작하며, `terraform init` 명령어로 다운로드된다.

```hcl
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  required_version = ">= 1.5.0"
}

provider "aws" {
  region = "ap-northeast-2"
}
```

**Resource**는 실제로 생성할 인프라 객체다. `resource "타입" "이름"` 형식으로 선언한다.

### State 파일

Terraform은 `.tfstate` 파일에 현재 인프라 상태를 저장한다. 이 파일이 Terraform의 핵심이자 가장 민감한 부분이다. 팀 단위로 작업할 때는 반드시 **Remote Backend**를 사용해야 한다.

```hcl
terraform {
  backend "s3" {
    bucket         = "my-terraform-state-bucket"
    key            = "prod/terraform.tfstate"
    region         = "ap-northeast-2"
    dynamodb_table = "terraform-lock"
    encrypt        = true
  }
}
```

S3는 상태 파일을 저장하고, DynamoDB는 동시 수정을 막는 **State Locking**을 담당한다.

### 변수와 출력

```hcl
# variables.tf
variable "environment" {
  description = "배포 환경 (dev/staging/prod)"
  type        = string
  default     = "dev"
}

variable "instance_type" {
  description = "EC2 인스턴스 타입"
  type        = string
}

# outputs.tf
output "alb_dns_name" {
  description = "Application Load Balancer DNS"
  value       = aws_lb.main.dns_name
}
```

### 모듈

모듈은 Terraform에서 코드 재사용의 핵심이다. 반복되는 인프라 패턴을 모듈로 추상화해 여러 환경에서 재사용할 수 있다.

```
infrastructure/
├── modules/
│   ├── vpc/
│   ├── ec2/
│   └── rds/
├── environments/
│   ├── dev/
│   ├── staging/
│   └── prod/
└── main.tf
```

---

## 실전 예제: AWS 3-Tier 아키텍처 구성

실무에서 자주 사용하는 VPC + EC2 + RDS 구성을 Terraform으로 코드화해본다.

### VPC 및 네트워크 구성

```hcl
# modules/vpc/main.tf

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "${var.project}-${var.environment}-vpc"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_subnet" "public" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = var.availability_zones[count.index]

  map_public_ip_on_launch = true

  tags = {
    Name = "${var.project}-${var.environment}-public-${count.index + 1}"
    Tier = "public"
  }
}

resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "${var.project}-${var.environment}-private-${count.index + 1}"
    Tier = "private"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project}-${var.environment}-igw"
  }
}

resource "aws_nat_gateway" "main" {
  count         = length(var.availability_zones)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name = "${var.project}-${var.environment}-nat-${count.index + 1}"
  }
}

resource "aws_eip" "nat" {
  count  = length(var.availability_zones)
  domain = "vpc"
}
```

### 보안 그룹 구성

```hcl
# modules/security_groups/main.tf

resource "aws_security_group" "alb" {
  name        = "${var.project}-${var.environment}-alb-sg"
  description = "ALB Security Group"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project}-${var.environment}-alb-sg"
  }
}

resource "aws_security_group" "app" {
  name        = "${var.project}-${var.environment}-app-sg"
  description = "Application Server Security Group"
  vpc_id      = var.vpc_id

  # ALB에서만 트래픽 허용
  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rds" {
  name        = "${var.project}-${var.environment}-rds-sg"
  description = "RDS Security Group"
  vpc_id      = var.vpc_id

  # 앱 서버에서만 DB 접근 허용
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
}
```

### RDS 구성

```hcl
# modules/rds/main.tf

resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-${var.environment}-db-subnet-group"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name = "${var.project}-${var.environment}-db-subnet-group"
  }
}

resource "aws_db_instance" "main" {
  identifier     = "${var.project}-${var.environment}-postgres"
  engine         = "postgres"
  engine_version = "15.4"
  instance_class = var.db_instance_class

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_encrypted     = true

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password  # 실무에서는 AWS Secrets Manager 사용 권장

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [var.rds_security_group_id]

  backup_retention_period = var.environment == "prod" ? 7 : 1
  deletion_protection     = var.environment == "prod" ? true : false
  skip_final_snapshot     = var.environment == "prod" ? false : true

  tags = {
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
```

### 환경별 변수 파일

```hcl
# environments/prod/terraform.tfvars

project            = "myapp"
environment        = "prod"
vpc_cidr           = "10.0.0.0/16"
availability_zones = ["ap-northeast-2a", "ap-northeast-2c"]
instance_type      = "t3.medium"
db_instance_class  = "db.t3.medium"
```

---

## 주의사항 및 트레이드오프

### 1. State 파일 보안에 각별히 주의

State 파일에는 RDS 패스워드, API 키 등 민감한 정보가 평문으로 저장될 수 있다. S3 버킷의 퍼블릭 액세스를 반드시 차단하고, 서버 사이드 암호화를 활성화해야 한다. 또한 **절대로 `.tfstate` 파일을 Git에 커밋하지 않는다.**

```gitignore
# .gitignore
*.tfstate
*.tfstate.backup
*.tfvars  # 민감한 변수가 포함된 경우
.terraform/
```

### 2. `terraform destroy`는 신중하게

`terraform destroy`는 관리하는 모든 리소스를 삭제한다. CI/CD 파이프라인에서 자동 실행되지 않도록 반드시 방어 로직을 추가하고, 프로덕션 리소스에는 `lifecycle` 블록으로 보호 설정을 한다.

```hcl
resource "aws_db_instance" "main" {
  # ...

  lifecycle {
    prevent_destroy = true  # terraform destroy 명령어로 삭제 불가
  }
}
```

### 3. Plan 결과를 반드시 리뷰한다

`terraform apply`를 바로 실행하지 말고, `terraform plan -out=tfplan` 으로 플랜 파일을 저장한 뒤 팀원과 리뷰 후 `terraform apply tfplan`을 실행하는 워크플로우를 정착시켜야 한다. **`~`(수정)와 `-/+`(재생성)의 차이를 반드시 구분해야 한다.** 특히 `-/+`는 기존 리소스를 삭제하고 새로 생성하므로, DB나 EC2에 이 표시가 뜨면 다운타임이 발생할 수 있다.

### 4. 모듈 버전 고정

외부 모듈이나 Provider 버전을 고정하지 않으면, 팀원마다 다른 버전을 사용하게 되어 예상치 못한 동작 차이가 생긴다. `required_providers`에 버전을 명시하고, `.terraform.lock.hcl` 파일을 Git에 커밋한다.

### 5. 기존 리소스 Import의 한계

콘솔에서 이미 수동으로 만든 리소스를 Terraform으로 가져오려면 `terraform import`를 사용한다. 하지만 Import는 State에만 등록할 뿐 `.tf` 파일을 자동으로 생성하지 않는다. Terraform 1.5부터 `import` 블록으로 개선되었고, `terraform plan`과 연동해 코드를 생성할 수 있지만, 복잡한 기존 인프라를 마이그레이션할 때는 상당한 작업 비용이 든다.

---

## 정리

Terraform은 단순한 도구가 아니라 **인프라를 소프트웨어처럼 개발하는 문화**를 만들어주는 도구다. 코드 리뷰, 버전 관리, 자동화 테스트의 원칙을 인프라에도 그대로 적용할 수 있게 된다.

실무 도입 시 권장하는 순서는 다음과 같다.

1. **Remote Backend 먼저 설정** — 팀 협업의 기본 전제
2. **디렉토리 구조 설계** — 모듈과 환경 분리를 초반에 잡을 것
3. **CI/CD 파이프라인 연동** — PR 시 `plan` 자동 실행, 머지 시 `apply`
4. **보안 정책 수립** — State 암호화, 민감 정보 Secrets Manager 위임
5. **점진적 마이그레이션** — 새 리소스부터 Terraform으로, 기존 리소스는 Import로

Terraform은 팀의 인프라 관리 역량을 한 단계 끌어올린다. 초기 학습 비용과 설계 투자가 있지만, 이후 환경 복제, 재현성 보장, 감사 추적 등 얻는 이점이 훨씬 크다. 지금 당장 작은 리소스 하나부터 코드로 관리해보길 권한다.