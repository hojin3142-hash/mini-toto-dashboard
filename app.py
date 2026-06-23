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

# Windows 등에서는 국기 이모지가 표시되지 않으므로 실제 국기 이미지를 사용한다.
KR_FLAG = "https://flagcdn.com/24x18/kr.png"  # 🇰🇷 태극기
ZA_FLAG = "https://flagcdn.com/24x18/za.png"  # 🇿🇦 남아공


def flag_img(url, width=20):
    """국기 이미지를 inline <img> 태그로 반환한다 (HTML 렌더링용)."""
    return (
        f'<img src="{url}" width="{width}" '
        'style="vertical-align:middle;margin:0 4px;border:1px solid #eee;">'
    )


st.markdown(
    f'<h1>⚽ 부산구축팀 월드컵 미니 토토 대시보드 '
    f'{flag_img(KR_FLAG, 32)}vs{flag_img(ZA_FLAG, 32)}</h1>',
    unsafe_allow_html=True,
)
st.markdown("한국과 남아공 경기의 스코어를 예측하고 상금을 차지하세요!")


# --- 데이터베이스 연동 (PostgreSQL) ---
DB_CONFIG = {
    "host": get_secret("LDAS_POSTGRES_HOST"),
    "port": get_secret("LDAS_POSTGRES_PORT"),
    "dbname": get_secret("LDAS_POSTGRES_DATABASE"),
    "user": get_secret("LDAS_POSTGRES_USER"),
    "password": get_secret("LDAS_POSTGRES_PASSWORD"),
    # Neon 등 클라우드 PostgreSQL은 SSL 연결이 필수다. 기본값 require.
    # 사내 DB처럼 SSL이 없는 경우 secret으로 "disable" 등을 지정해 덮어쓸 수 있다.
    "sslmode": get_secret("LDAS_POSTGRES_SSLMODE", "require"),
}

# DB 컬럼명 <-> 화면 표시용 한글 컬럼명 매핑
COLUMN_MAP = {
    "name": "이름",
    "kr_score": "한국_스코어",
    "za_score": "남아공_스코어",
    "bet_amount": "베팅금액",
}


def get_result_label(kr, za):
    """스코어를 비교해 '한국 승' / '남아공 승' / '무승부' 라벨을 반환한다."""
    if kr > za:
        return "한국 승"
    if za > kr:
        return "남아공 승"
    return "무승부"


def get_connection():
    """매 쿼리마다 새 PostgreSQL 커넥션을 생성한다 (여러 유저 동시 접속 대비)."""
    return psycopg2.connect(**DB_CONFIG)


@st.cache_resource
def init_db():
    """앱 최초 실행 시 테이블 생성 및 스키마 마이그레이션을 수행한다 (세션 간 1회만 실행)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS toto_bets (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(50),
                    kr_score INT,
                    za_score INT,
                    bet_amount INT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                """
            )

            # 상대 팀이 멕시코 -> 남아공으로 변경됨.
            # 기존 'mx_score'(멕시코) 컬럼이 있으면 'za_score'(남아공)로 이름을 바꾸고,
            # 팀이 달라졌으므로 그동안 등록된 예측(스코어)을 모두 리셋한다.
            # information_schema로 컬럼 존재 여부를 확인하므로 최초 1회만 실행된다.
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'toto_bets' AND column_name = 'mx_score';
                """
            )
            if cur.fetchone():
                cur.execute("ALTER TABLE toto_bets RENAME COLUMN mx_score TO za_score;")
                cur.execute("DELETE FROM toto_bets;")  # 등록된 스코어 전체 리셋
        conn.commit()
    return True


def insert_bet(name, kr_score, za_score, bet_amount):
    """베팅 1건을 DB에 INSERT 한다."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO toto_bets (name, kr_score, za_score, bet_amount)
                VALUES (%s, %s, %s, %s);
                """,
                (name, kr_score, za_score, bet_amount),
            )
        conn.commit()


def get_bets():
    """DB에서 모든 베팅을 SELECT 하여 화면 표시용 DataFrame으로 반환한다."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT name, kr_score, za_score, bet_amount
                FROM toto_bets
                ORDER BY id ASC;
                """
            )
            rows = cur.fetchall()

    df = pd.DataFrame(
        rows, columns=["name", "kr_score", "za_score", "bet_amount"]
    )
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
            za_score = st.number_input("🇿🇦 남아공 스코어", min_value=0, step=1)

        bet_amount = st.number_input("베팅 금액 (원)", min_value=1000, step=1000, value=10000)

        submitted = st.form_submit_button("예측 제출")
        if submitted and name:
            # 새 데이터 추가 (DB INSERT)
            insert_bet(name, int(kr_score), int(za_score), int(bet_amount))
            st.success(f"{name}님의 예측이 등록되었습니다!")

    # 한번 등록한 예측은 수정할 수 없도록 수정 기능을 제거했다.
    st.markdown("---")
    st.caption("⚠️ 한번 등록한 예측은 수정할 수 없습니다. 신중하게 입력해 주세요.")

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

    if not df.empty:
        df = df.copy()
        # 한국 득점 기준 오름차순 정렬 (동점이면 남아공 득점 기준)
        df = df.sort_values(
            by=["한국_스코어", "남아공_스코어"], ascending=True
        ).reset_index(drop=True)

        # --- 스코어 분포표 (행: 한국 0~4점 / 열: 남아공 0~4점, 값: 예측한 사람 이름) ---
        st.subheader("🎯 스코어 분포표")
        st.caption("행 = 한국 득점, 열 = 남아공 득점, 칸 = 해당 스코어를 예측한 사람 이름 (중복 시 콤마 구분)")

        scores = list(range(5))  # 0~4점 고정

        # 표를 HTML로 직접 그려 헤더에 국기 이미지를 넣는다.
        html = ['<table style="border-collapse:collapse;width:100%;text-align:center;">']
        # 헤더 행 (남아공 0~4)
        html.append("<tr>")
        html.append(
            '<th style="border:1px solid #ddd;padding:6px;background:#f5f7fa;">'
            f"{flag_img(KR_FLAG)}한국 ＼ {flag_img(ZA_FLAG)}남아공</th>"
        )
        for za in scores:
            html.append(
                '<th style="border:1px solid #ddd;padding:6px;background:#f5f7fa;white-space:nowrap;">'
                f"{flag_img(ZA_FLAG)}남아공 {za}</th>"
            )
        html.append("</tr>")
        # 본문 행 (한국 0~4)
        for kr in scores:
            html.append("<tr>")
            html.append(
                '<th style="border:1px solid #ddd;padding:6px;background:#f5f7fa;white-space:nowrap;">'
                f"{flag_img(KR_FLAG)}한국 {kr}</th>"
            )
            for za in scores:
                names = df.loc[
                    (df["한국_스코어"] == kr) & (df["남아공_스코어"] == za), "이름"
                ].tolist()
                cell = ", ".join(str(n) for n in names)
                if cell:
                    style = (
                        "border:1px solid #ddd;padding:6px;"
                        "background:rgba(33,118,209,0.12);color:#0b2545;font-weight:600;"
                    )
                else:
                    style = "border:1px solid #ddd;padding:6px;color:#cccccc;"
                html.append(f'<td style="{style}">{cell}</td>')
            html.append("</tr>")
        html.append("</table>")

        st.markdown("".join(html), unsafe_allow_html=True)

        st.subheader("예측 데이터")
        # 이름 뒤에 점수에 따른 승무패 구분 컬럼 추가
        df.insert(
            1,
            "예측결과",
            df.apply(
                lambda x: get_result_label(x["한국_스코어"], x["남아공_스코어"]), axis=1
            ),
        )

        # 한국/남아공 컬럼 헤더에 국기 이미지를 넣기 위해 HTML 표로 출력한다.
        df_view = df.copy()
        df_view["베팅금액"] = df_view["베팅금액"].apply(lambda v: f"{v:,.0f} 원")
        df_view = df_view.rename(
            columns={
                "한국_스코어": f"{flag_img(KR_FLAG)}한국 스코어",
                "남아공_스코어": f"{flag_img(ZA_FLAG)}남아공 스코어",
            }
        )
        # escape=False 로 헤더의 <img> 태그가 렌더링되도록 한다.
        st.markdown(
            df_view.to_html(escape=False, index=False),
            unsafe_allow_html=True,
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
    actual_za = col2.number_input("실제 🇿🇦 남아공 스코어", min_value=0, step=1, key="act_za")

    if st.button("결과 정산하기"):
        df = get_bets()  # 정산 시점의 최신 데이터를 DB에서 조회
        if df.empty:
            st.warning("정산할 베팅 데이터가 없습니다.")
        else:
            # 1순위: 스코어 완벽 적중자
            exact_winners = df[
                (df["한국_스코어"] == actual_kr) & (df["남아공_스코어"] == actual_za)
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
                actual_result = get_result_label(actual_kr, actual_za)

                df = df.copy()
                df["예측결과"] = df.apply(
                    lambda x: get_result_label(x["한국_스코어"], x["남아공_스코어"]),
                    axis=1,
                )
                match_winners = df[df["예측결과"] == actual_result]

                if not match_winners.empty:
                    st.success(
                        f"👏 스코어는 틀렸지만, '{actual_result}' 결과를 맞춘 사람이 {len(match_winners)}명 있습니다! (상금 이월 또는 2순위 분배)"
                    )
                    st.dataframe(
                        match_winners[
                            ["이름", "한국_스코어", "남아공_스코어", "베팅금액"]
                        ].style.format({"베팅금액": "{:,.0f} 원"}),
                        use_container_width=True,
                    )
                else:
                    st.error(
                        "경기 결과(승무패)를 맞춘 사람도 없습니다. 전체 상금은 주최자가 가져가거나 다음 경기로 이월됩니다! 😈"
                    )
