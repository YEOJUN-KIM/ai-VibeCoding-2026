# 1. 머신러닝 개발 환경

## 목적

웹 애플리케이션과 머신러닝 실험이 같은 버전의 패키지를 사용하고, 다른 컴퓨터에서도 환경을 다시 만들 수 있게 합니다.

현재 프로젝트는 Windows와 Python 3.14 계열을 사용합니다. 기존 `.venv`를 그대로 사용하되 머신러닝 의존성은 `requirements-ml.txt`에서 관리합니다. 이 파일이 기존 `requirements.txt`도 불러오므로 명령 한 번으로 전체 환경을 설치합니다.

## 처음 사용할 패키지

| 패키지 | 역할 |
|---|---|
| `numpy` | 수치 배열과 계산 |
| `pandas` | 분봉·뉴스·특징 데이터를 표 형태로 처리 |
| `scipy` | 통계와 과학 계산 |
| `scikit-learn` | 전처리, 기초 모델, 시간순 검증과 평가 |
| `matplotlib`, `seaborn` | 학습 결과와 손익 시각화 |
| `joblib` | 학습한 모델 저장과 로딩 |

LightGBM은 로지스틱 회귀와 scikit-learn 기준 모델을 완성한 다음 추가합니다. Jupyter Notebook은 탐색용으로만 사용하고, 최종 데이터 생성·학습·평가는 명령행에서 재현 가능한 Python 모듈로 옮깁니다.

## 가장 쉬운 설치 방법

프로젝트 루트에서 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-ml.ps1
```

스크립트는 `.venv`가 없을 때만 만들고, 고정된 패키지를 설치한 뒤 검증용 모델의 학습·저장·재로딩까지 실행합니다. 마지막 JSON에서 `"round_trip_verified": true`가 나오면 성공입니다.

## 직접 설치하는 방법

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ml.txt
.\.venv\Scripts\python.exe -m auto_trader.ml.verify_environment
```

PowerShell 실행 정책 때문에 `Activate.ps1`가 막힐 수 있으므로 활성화 없이 `.venv`의 Python을 직접 호출합니다. 설치된 상세 버전은 다음 명령으로 확인할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m pip list
.\.venv\Scripts\python.exe -c "import sklearn; sklearn.show_versions()"
```

LightGBM은 4단계의 로지스틱 회귀 기준 모델을 완성한 다음 추가합니다.

## 디렉터리

```text
auto_trader/ml/          데이터 생성, 학습, 평가와 추론 코드
models/                  승인된 모델과 메타데이터, Git 제외
artifacts/ml/            그래프와 실험 결과, 필요한 결과만 선별 보존
tests/                   특징·정답·누출·추론 회귀 테스트
requirements-ml.txt      재현 가능한 ML 패키지 버전
```

`models/`에는 모델뿐 아니라 학습 기간, 특징 목록, 비용 가정, 평가 결과와 코드 버전을 함께 저장해야 합니다. 모델 파일만 있으면 어떤 조건에서 만들어졌는지 확인할 수 없습니다.

## 완료 결과 (2026-10-02)

- [x] 새 가상환경에서 한 번의 설치 절차로 실행된다.
- [x] Python 3.14에서 확인한 패키지 버전이 `requirements-ml.txt`에 고정됐다.
- [x] `auto_trader.ml.verify_environment`가 작은 예제 모델을 학습하고 저장한 뒤 다시 불러온다.
- [x] 저장 전후 예측이 같은지 자동 테스트한다.
- [x] 기존 웹 애플리케이션 테스트가 계속 통과한다.

## 공식 참고 자료

- [Python 3.14 가상환경](https://docs.python.org/3/library/venv.html)
- [scikit-learn 설치](https://scikit-learn.org/stable/install)
- [pandas 설치](https://pandas.pydata.org/pandas-docs/stable/getting_started/install.html)
- [LightGBM Python 패키지](https://lightgbm.readthedocs.io/en/stable/Python-Intro.html)
