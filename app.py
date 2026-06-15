import os

import pandas as pd
import psycopg2
import psycopg2.extras
import streamlit as st
from dotenv import load_dotenv

# .env 파일에서 DB 접속 정보 로드 (로컬 개발용)
load_dotenv()


def get_secret(key, default=None):
    """접속 정보를 읽는다.

    Streamlit Community Cloud에서는 앱 설정의 Secrets(st.secrets)를 우선 사용하고,
    로컬에서는 .env(환경변수)에서 읽는다.
    """
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:  # noqa: BLE001 - secrets.toml이 없으면 무시하고 .env 사용
        pass
    return os.getenv(key, default)

# 페이지 설정
st.set_page_config(page_title="미니 토토 대시보드", page_icon="⚽", layout="wide")

st.title("⚽ 부산운영팀 월드컵 미니 토토 대시보드 🇰🇷vs🇲🇽")
st.markdown("한국과 멕시코 경기의 스코어를 예측하고 상금을 차지하세요!")


# --- 데이터베이스 연동 (PostgreSQL) ---
DB_CONFIG = {
    "host": get_secret("LDAS_POSTGRES_HOST"),
    "port": get_secret("LDAS_POSTGRES_PORT"),
    "dbname": get_secret("LDAS_POSTGRES_DATABASE"),
    "user": get_secret("LDAS_POSTGRES_USER"),
    "password": get_secret("LDAS_POSTGRES_PASSWORD"),
}

# DB 컬럼명 <-> 화면 표시용 한글 컬럼명 매핑
COLUMN_MAP = {
    "name": "이름",
    "kr_score": "한국_스코어",
    "mx_score": "멕시코_스코어",
    "bet_amount": "베팅금액",
}


def get_connection():
    """매 쿼리마다 새 PostgreSQL 커넥션을 생성한다 (여러 유저 동시 접속 대비)."""
    return psycopg2.connect(**DB_CONFIG)


@st.cache_resource
def init_db():
    """앱 최초 실행 시 테이블이 없으면 생성한다 (세션 간 1회만 실행)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS toto_bets (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(50),
                    kr_score INT,
                    mx_score INT,
                    bet_amount INT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                """
            )
        conn.commit()
    return True


def insert_bet(name, kr_score, mx_score, bet_amount):
    """베팅 1건을 DB에 INSERT 한다."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO toto_bets (name, kr_score, mx_score, bet_amount)
                VALUES (%s, %s, %s, %s);
                """,
                (name, kr_score, mx_score, bet_amount),
            )
        conn.commit()


def get_bets():
    """DB에서 모든 베팅을 SELECT 하여 화면 표시용 DataFrame으로 반환한다."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT name, kr_score, mx_score, bet_amount
                FROM toto_bets
                ORDER BY id ASC;
                """
            )
            rows = cur.fetchall()

    df = pd.DataFrame(rows, columns=["name", "kr_score", "mx_score", "bet_amount"])
    # DB 컬럼명을 화면 표시용 한글 컬럼명으로 변경
    df = df.rename(columns=COLUMN_MAP)
    return df


# 테이블 초기화 (없으면 생성)
try:
    init_db()
except Exception as e:  # noqa: BLE001
    st.error(f"데이터베이스 연결에 실패했습니다. 접속 정보(.env)를 확인해 주세요.\n\n{e}")
    st.stop()


# --- 사이드바: 베팅 참여 입력폼 ---
with st.sidebar:
    st.header("🎟️ 베팅 참여하기")
    with st.form("bet_form", clear_on_submit=True):
        name = st.text_input("참가자 이름")

        col1, col2 = st.columns(2)
        with col1:
            kr_score = st.number_input("🇰🇷 한국 스코어", min_value=0, step=1)
        with col2:
            mx_score = st.number_input("🇲🇽 멕시코 스코어", min_value=0, step=1)

        bet_amount = st.number_input("베팅 금액 (원)", min_value=1000, step=1000, value=5000)

        submitted = st.form_submit_button("예측 제출")
        if submitted and name:
            # 새 데이터 추가 (DB INSERT)
            insert_bet(name, int(kr_score), int(mx_score), int(bet_amount))
            st.success(f"{name}님의 예측이 등록되었습니다!")

# --- 메인 화면: 탭 구성 ---
tab1, tab2 = st.tabs(["📊 현재 참여 현황", "🏆 경기 결과 정산"])


# 1. 참여 현황 탭 (5초마다 자동 갱신되는 폴링 프래그먼트)
@st.fragment(run_every="5s")
def render_status():
    # 5초마다 DB에서 최신 데이터를 다시 SELECT (다른 유저의 베팅도 실시간 반영)
    df = get_bets()
    total_pool = df["베팅금액"].sum() if not df.empty else 0

    col1, col2 = st.columns(2)
    col1.metric("총 참여자 수", f"{len(df)} 명")
    col2.metric("현재 총 상금 풀", f"{total_pool:,.0f} 원")

    st.caption("⏱️ 5초마다 자동으로 최신 데이터를 불러옵니다.")
    st.subheader("예측 데이터")
    if not df.empty:
        # 보기 좋게 데이터프레임 출력
        st.dataframe(
            df.style.format({"베팅금액": "{:,.0f} 원"}), use_container_width=True
        )
    else:
        st.info("아직 참여자가 없습니다. 사이드바에서 예측을 제출해 주세요.")


with tab1:
    render_status()

# 2. 결과 정산 탭
with tab2:
    st.subheader("경기 종료 후 결과 입력")

    col1, col2 = st.columns(2)
    actual_kr = col1.number_input("실제 🇰🇷 한국 스코어", min_value=0, step=1, key="act_kr")
    actual_mx = col2.number_input("실제 🇲🇽 멕시코 스코어", min_value=0, step=1, key="act_mx")

    if st.button("결과 정산하기"):
        df = get_bets()  # 정산 시점의 최신 데이터를 DB에서 조회
        if df.empty:
            st.warning("정산할 베팅 데이터가 없습니다.")
        else:
            # 1순위: 스코어 완벽 적중자
            exact_winners = df[
                (df["한국_스코어"] == actual_kr) & (df["멕시코_스코어"] == actual_mx)
            ]

            st.markdown("---")
            total_pool = df["베팅금액"].sum()

            if not exact_winners.empty:
                st.success(f"🎉 스코어 완벽 적중자가 {len(exact_winners)}명 있습니다!")

                # 상금 분배 (맞춘 사람들의 베팅 비율대로 1/n 또는 비율 분배)
                exact_winners = exact_winners.copy()
                winner_bet_sum = exact_winners["베팅금액"].sum()
                exact_winners["획득상금"] = (
                    exact_winners["베팅금액"] / winner_bet_sum
                ) * total_pool

                st.dataframe(
                    exact_winners[["이름", "베팅금액", "획득상금"]].style.format(
                        {"베팅금액": "{:,.0f} 원", "획득상금": "{:,.0f} 원"}
                    ),
                    use_container_width=True,
                )
                st.balloons()
            else:
                st.info("😢 스코어를 정확히 맞춘 사람이 없습니다.")

                # 2순위: 승무패 결과 적중자 구하기 로직 (간단 구현)
                actual_result = (
                    "한국 승"
                    if actual_kr > actual_mx
                    else "멕시코 승"
                    if actual_mx > actual_kr
                    else "무승부"
                )

                def get_prediction_result(kr, mx):
                    return "한국 승" if kr > mx else "멕시코 승" if mx > kr else "무승부"

                df = df.copy()
                df["예측결과"] = df.apply(
                    lambda x: get_prediction_result(x["한국_스코어"], x["멕시코_스코어"]),
                    axis=1,
                )
                match_winners = df[df["예측결과"] == actual_result]

                if not match_winners.empty:
                    st.success(
                        f"👏 스코어는 틀렸지만, '{actual_result}' 결과를 맞춘 사람이 {len(match_winners)}명 있습니다! (상금 이월 또는 2순위 분배)"
                    )
                    st.dataframe(
                        match_winners[
                            ["이름", "한국_스코어", "멕시코_스코어", "베팅금액"]
                        ].style.format({"베팅금액": "{:,.0f} 원"}),
                        use_container_width=True,
                    )
                else:
                    st.error(
                        "경기 결과(승무패)를 맞춘 사람도 없습니다. 전체 상금은 주최자가 가져가거나 다음 경기로 이월됩니다! 😈"
                    )
