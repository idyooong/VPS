import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import datetime
import base64
import time
import random
import streamlit.components.v1 as components

# [설정] 구글 시트 클라이언트
def get_gspread_client():
    creds_dict = json.loads(st.secrets["gcp_service_account"])
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

# [수정됨] 엑셀 스키마 보호를 위해 ID 분리 (None_T2: 정상 진단용 / None_T1: 심각도 없음용)
GROUPS = {
    "group_A": {
        "task1": ["OCD", "MDD", "None_T1", "GAD"],  # 질환 종류 맞추기용 4개
        "task2": ["Moderate", "Mild", "Severe", "None_T2"] # 우울증 심각도 평가용 4개
    }
}

# [수정됨] 분리된 ID 반영
GROUND_TRUTH = {
    "None_T2": {"diagnosis": "주요우울장애(MDD)", "severity": "없음(None)"},
    "None_T1": {"diagnosis": "질환 없음", "severity": "N/A"},
    "Mild": {"diagnosis": "주요우울장애(MDD)", "severity": "경도(Mild)"},
    "Moderate": {"diagnosis": "주요우울장애(MDD)", "severity": "중등도(Moderate)"},
    "Severe": {"diagnosis": "주요우울장애(MDD)", "severity": "중증(Severe)"},
    "OCD": {"diagnosis": "강박장애", "severity": "N/A"},
    "GAD": {"diagnosis": "범불안장애", "severity": "N/A"},
    "MDD": {"diagnosis": "주요우울장애", "severity": "N/A"}
}

VIDEO_LENGTHS = {
    "None_T2": 210, "Mild": 230, "Moderate": 250, "Severe": 208,
    "None_T1": 236, "OCD": 263, "GAD": 235, "MDD": 189
}


def main():
    if "global_start_time" not in st.session_state:
        st.session_state.global_start_time = time.time()
    # 실험 전체 및 타임 로그 관리를 위한 독립 세션 초기화
    if 'time_logs' not in st.session_state:
        st.session_state.time_logs = {}
    st.set_page_config(page_title="HCI 실험", layout="wide")
    hide_streamlit_style = """
            <style>
            #MainMenu {visibility: hidden;}
            header {visibility: hidden;}
            footer {visibility: hidden;}
            .viewerBadge_container {display: none !important;}
            </style>
            """
    st.markdown(hide_streamlit_style, unsafe_allow_html=True)

    if 'step' not in st.session_state:
        st.session_state.step = 'instructions'
        st.session_state.data = {}
        st.session_state.task1_videos = []
        st.session_state.task2_videos = []
        st.session_state.v_idx = 0 

    participant_view()

def participant_view():
    # [수정됨] 화면이 렌더링될 때마다 강제로 스크롤을 최상단으로 끌어올리는 JS 주입
    js = """
    <script>
        var body = window.parent.document.querySelector(".main");
        if (body) {
            body.scrollTop = 0;
        }
        window.parent.scrollTo(0, 0);
    </script>
    """
    components.html(js, height=0)

    step = st.session_state.step

    # ---------------------------------------------------------
    # [Step] 기본 인적 사항
    # ---------------------------------------------------------
    if step == 'instructions':
        st.title("실험 진행 안내사항")
        st.markdown("""
            <div style='background-color: #f0f2f6; padding: 20px; border-radius: 10px; line-height: 1.8;'>
            <p>1.  <b>데스크탑 PC 환경에서 진행해 주십시오.</b></p>
            <p>2.  본 실험은 영상의 음성과 아바타의 모션을 평가하므로, 반드시 <b>이어폰을 착용한 상태</b>로 진행해 주십시오.</p>
            <p>3.  <b>각 가상 환자 영상의 길이는 약 3~4분 내외이며, 단 1회 시청을 원칙으로 합니다.</b> 재생 중 환자의 발화 내용과 관찰 가능한 단서를 놓치지 않도록 화면과 음성에 완전히 집중해 주십시오.</p>
            <p>4.  실험 도중 절대로 <b>‘새로고침(F5)’</b>이나 <b>‘뒤로 가기’</b> 버튼을 누르지 마십시오.</p>
            <p>5.  도중에 창을 닫으면 데이터가 소실되어 실험을 처음부터 다시 시작해야 합니다. <b>반드시 한 번에 끝까지 진행해 주십시오.</b></p>
        </div> 
        """, unsafe_allow_html=True)
        st.markdown("""
        <div style='margin-top: 20px; padding: 20px; border-left: 5px solid #1f77b4;'>
            <h4>본 실험은 두 가지 파트로 나뉘어 진행됩니다.</h4>
            <ul>
                <li><b> [Part 1]</b> 환자의 <b>질환 종류</b>를 진단하는 과제</li>
                <li><b> [Part 2]</b> 환자의 동일한 질환에 대한 <b>심각도</b>를 평가하는 과제</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)
        st.write("")
        if st.button("위 안내사항을 모두 확인하였으며, 인적사항을 입력하겠습니다."):
            with st.spinner("실험 환경을 설정 중입니다..."):
                client = get_gspread_client()
                sheet = client.open("ExperimentDB").worksheet("groups")
                data = pd.DataFrame(sheet.get_all_records())
                
                min_group = data.loc[data['count'].idxmin()]
                
                # 원본 리스트를 복사해옴
                t1_vids = GROUPS[min_group['group_id']]["task1"].copy()
                t2_vids = GROUPS[min_group['group_id']]["task2"].copy()
                
                # [핵심] 피험자마다 영상 순서를 무작위로 섞음 (Randomization)
                random.shuffle(t1_vids)
                random.shuffle(t2_vids)
                
                # 섞인 리스트를 세션에 할당
                st.session_state.task1_videos = t1_vids
                st.session_state.task2_videos = t2_vids
                
                st.session_state.data['group_id'] = min_group['group_id']
                
                row_index = data.index[data['group_id'] == min_group['group_id']][0] + 2
                sheet.update_cell(row_index, 2, int(min_group['count']) + 1)
                
                st.session_state.step = 'demography'
                st.session_state.v_idx = 0
                st.rerun()
    # ---------------------------------------------------------
    # [Step] 실험 안내사항
    # ---------------------------------------------------------
    elif step == 'demography':
        st.title("가상환자 평가 실험")
        st.subheader("기본 인적 사항 및 임상 경험 사전 조사")
        
        with st.form("demography"):
            st.markdown("**[기본 인적 사항]**")
            st.session_state.data['name'] = st.text_input("**1. 성함**")
            st.session_state.data['gender'] = st.radio("**2. 성별**", options=["남성", "여성"], index=None, horizontal=True)
            st.session_state.data['birth_date'] = st.text_input("**3. 생년월일 (예: 010101)**", max_chars=6)
            st.session_state.data['major'] = st.text_input("**4. 전공 분야 (예: 의학과, 간호학과, 심리학과 등)**")
            st.session_state.data['degree'] = st.text_input("**5. 현재 소속 및 교육/수련 단계 (예: 의과대학생, 전공의, 정신건강임상심리수련생, 전문의, 상담사 등)**")
            
            st.divider()
            st.markdown("**[임상 및 훈련 경험]**")
            st.markdown("**6. 과거 임상 실습이나 면담 훈련을 위한 '환자 시뮬레이션 훈련 또는 실제 임상 참관' 경험 횟수를 기재해 주십시오. (※ 경험이 없는 항목은 0으로 두십시오.)**")
            
            cb_none = st.checkbox("해당되는 훈련 및 참관 경험이 전혀 없음 (※ 이 항목 체크 시 아래 모든 횟수는 0이어야 합니다)")
            
            # 팩트 체크: st.form 내부에서는 동적 UI 생성이 불가하므로 number_input으로 횟수를 직접 받음
            col1, col2 = st.columns(2)
            with col1:
                cnt_shadowing = st.number_input("지도감독자 또는 선배의 실제 진료 참관 (회)", min_value=0, step=1)
                cnt_peer = st.number_input("동료/선후배 간 역할극 (회)", min_value=0, step=1)
                cnt_sp = st.number_input("표준화 환자(훈련된 모의 환자 연기자) 대면 면담 (회)", min_value=0, step=1)
            with col2:
                cnt_text = st.number_input("텍스트 기반 환자 시나리오 챗봇 (회)", min_value=0, step=1)
                cnt_vp = st.number_input("화면 속 아바타/가상 환자 시뮬레이션 (회)", min_value=0, step=1)
                cnt_video = st.number_input("사전 녹화된 실제 환자 또는 모의 환자 영상 관찰 훈련 (회)", min_value=0, step=1)
                
            cnt_other = st.number_input("기타 훈련 (회)", min_value=0, step=1)
            other_text = st.text_input("기타 훈련 내용 기재 (※ 기타 훈련 횟수가 1 이상인 경우 반드시 기재)")

            st.session_state.data['clinical_experience'] = st.radio("**7. 실제 정신건강 환자를 직접 상담(면담)한 경험이 있습니까?**", options=["예", "아니요"], index=None, horizontal=True)
            st.session_state.data['consultation_count'] = st.number_input("**7-1. 직접 상담(면담) 횟수 (※ 경험이 '예'인 경우 기재, 없으면 0)**", min_value=0, max_value=10000, value=0, step=1)
            st.session_state.data['clinical_years'] = st.number_input("**8. 임상 경력 년차**", min_value=0, max_value=50, value=0, step=1)
            st.session_state.data['consulted_disorders'] = st.text_input("**9. 실제 면담 경험이 있다면, 어떤 질환의 환자를 면담해 보셨나요? (※ 해당사항 없으면 '없음' 기재)**")
            
            st.session_state.data['certifications'] = st.text_area(
                "**10. 보유하고 있는 상담 및 정신의학 자격증 이름 및 발급일자 전체 기재**",
                placeholder="※ 정확한 명칭과 급수를 기재해 주십시오. (예: 정신건강임상심리사 1급(2026-01-01))\n※ 해당 사항이 없을 경우 '없음'이라고 기재해 주십시오."
            )
            
            st.session_state.data['communication_difficulty'] = st.text_area(
                "**11. 실제 환자 또는 모의 환자를 면담하면서 가장 어려웠던 점(의사소통, 증상 파악, 진단, 라포 형성 등)을 자유롭게 작성해 주십시오.**",
                placeholder="※ 임상/실습 경험이 있는 경우: 면담 시 가장 큰 어려움을 느꼈던 경험\n※ 임상/실습 경험이 없는 경우: 향후 환자 대면 시 가장 어려울 것으로 예상되는 점"
            )

            st.divider()
            st.markdown("**[진단 역량 및 활용 정보 평가]**")
            st.session_state.data['dsm_familiarity'] = st.radio(
                "**12. DSM-5 진단기준에 얼마나 익숙하십니까?**",
                ["1점 - 전혀 익숙하지 않다", "2점 - 익숙하지 않은 편이다", "3점 - 보통이다", "4점 - 익숙한 편이다", "5점 - 매우 익숙하다"], index=None
            )
            
            st.session_state.data['diag_confidence'] = st.radio(
                "**13. 본인의 정신질환 감별 능력에 대해 얼마나 자신이 있습니까?**",
                ["1점 - 매우 자신 없음", "2점 - 자신 없는 편이다", "3점 - 보통이다", "4점 - 자신 있는 편이다", "5점 - 매우 자신 있다"], index=None
            )
            
            # 팩트 체크: 우선순위 지정을 위한 개별 selectbox 분리
            st.markdown("**14. 실제 환자를 진단할 때 가장 중요하게 활용하는 정보를 중요한 순서대로 3개 선택해 주십시오.**")
            cues_options = ["발화 내용", "표정", "시선", "몸짓", "음성(말투, 속도, 억양)", "정동(Affect)", "병력 및 과거력", "기타"]
            col_cue1, col_cue2, col_cue3 = st.columns(3)
            with col_cue1: st.session_state.data['important_cue_1'] = st.selectbox("1순위", cues_options, index=None)
            with col_cue2: st.session_state.data['important_cue_2'] = st.selectbox("2순위", cues_options, index=None)
            with col_cue3: st.session_state.data['important_cue_3'] = st.selectbox("3순위", cues_options, index=None)
            st.session_state.data['important_cue_other'] = st.text_input("14번 문항에서 '기타'를 선택한 경우 구체적인 내용을 기재해 주십시오.")
            
            st.session_state.data['frequent_disorders'] = st.text_input("**15. 가장 많이 접해본 정신질환은 무엇입니까? (복수 응답 가능, 없으면 '없음' 기재)**")
            st.session_state.data['confident_disorders'] = st.text_input("**16. 정신질환을 평가할 때 본인이 가장 자신 있는 질환은 무엇인가요? (복수 응답 가능, 없으면 '없음' 기재)**")

            # ---------------------------------------------------------
            # 제출 및 무결성 검증 (Validation)
            # ---------------------------------------------------------
            if st.form_submit_button("실험 시작하기"):
                # 1. 필수 문항 응답 확인
                required_keys = [
                    'name', 'gender', 'birth_date', 'major', 'degree', 'clinical_experience', 
                    'consulted_disorders', 'certifications', 'communication_difficulty', 
                    'dsm_familiarity', 'diag_confidence', 'frequent_disorders', 'confident_disorders',
                    'important_cue_1', 'important_cue_2', 'important_cue_3'
                ]
                if not all(st.session_state.data.get(k) for k in required_keys):
                    st.warning("모든 문항을 빠짐없이 입력해 주십시오.")
                    st.stop()
                
                # 2. 15번 문항 논리 통제 (중복 선택 방지)
                selected_cues = [
                    st.session_state.data['important_cue_1'], 
                    st.session_state.data['important_cue_2'], 
                    st.session_state.data['important_cue_3']
                ]
                if len(set(selected_cues)) != 3:
                    st.error("14번 문항: 1순위, 2순위, 3순위에 서로 다른 항목을 선택해 주십시오. (중복 불가)")
                    st.stop()
                
                if "기타" in selected_cues and not st.session_state.data['important_cue_other'].strip():
                    st.error("14번 문항에서 '기타'를 선택하셨습니다. 아래 텍스트 칸에 구체적인 내용을 기재해 주십시오.")
                    st.stop()
                
                # 3. 임상 경험 모순 통제
                exp = st.session_state.data['clinical_experience']
                count = st.session_state.data['consultation_count']
                years = st.session_state.data['clinical_years']
                
                if exp == "예" and (count <= 0):
                    st.error("환자 상담 경험이 '예'라고 응답하셨습니다. 상담 횟수(8-1번)를 1 이상으로 기재해 주십시오.")
                    st.stop()
                if exp == "아니요":
                    if count > 0:
                        st.error("환자 상담 경험이 '아니요'인 경우 상담 횟수는 0이어야 합니다.")
                        st.stop()
                    st.session_state.data['consulted_disorders'] = "N/A"

                # 4. 7번 시뮬레이션 경험 횟수 검증 및 데이터 직렬화
                total_sim_count = cnt_shadowing + cnt_peer + cnt_sp + cnt_text + cnt_vp + cnt_video + cnt_other
                
                if cb_none and total_sim_count > 0:
                    st.error("6번 문항 오류: '경험 전혀 없음'에 체크하셨으나, 하단에 입력된 훈련 횟수가 존재합니다. 논리적 모순을 수정해 주십시오.")
                    st.stop()
                
                if not cb_none and total_sim_count == 0:
                    st.error("6번 문항 오류: 어떠한 훈련 경험도 입력되지 않았습니다. 경험이 없다면 '경험 전혀 없음'에 체크해 주십시오.")
                    st.stop()
                
                if cnt_other > 0 and not other_text.strip():
                    st.error("6번 문항: '기타 훈련' 횟수가 입력되었습니다. 해당 훈련의 구체적인 내용을 기재해 주십시오.")
                    st.stop()

                # DB 저장을 위한 7번 문항 결과 직렬화
                sim_result = []
                if cb_none: 
                    sim_result.append("경험 없음")
                else:
                    if cnt_shadowing > 0: sim_result.append(f"진료참관({cnt_shadowing}회)")
                    if cnt_peer > 0: sim_result.append(f"역할극({cnt_peer}회)")
                    if cnt_sp > 0: sim_result.append(f"SP면담({cnt_sp}회)")
                    if cnt_text > 0: sim_result.append(f"텍스트챗봇({cnt_text}회)")
                    if cnt_vp > 0: sim_result.append(f"가상환자({cnt_vp}회)")
                    if cnt_video > 0: sim_result.append(f"영상관찰({cnt_video}회)")
                    if cnt_other > 0: sim_result.append(f"기타[{other_text}]({cnt_other}회)")

                st.session_state.data['simulation_experience'] = ", ".join(sim_result)
                
                # 검증 완료 후 이동
                st.session_state.step = 'task1_instructions'
                st.rerun()
    # ---------------------------------------------------------
    # [Step] TASK 1 - Instructions (Task 1 사전 안내)
    # ---------------------------------------------------------
    elif step == 'task1_instructions':
        st.title("파트 1: 질환 종류 진단 평가 안내")
        st.markdown("<br>", unsafe_allow_html=True)
        
        html_t1 = """
        <div style="background-color: #ffffff; padding: 35px; border-radius: 12px; border-left: 6px solid #1f77b4; box-shadow: 0px 4px 12px rgba(0,0,0,0.05);">
            <h3 style="color: #1f77b4; margin-top: 0; margin-bottom: 20px;"> Task 1 진행 방식</h3>
            <p style="font-size: 16px; line-height: 1.6; color: #333;">
                지금부터 <b>첫 번째 파트(Task 1)</b>가 시작됩니다.<br>
                본 파트에서는 가상 환자 영상을 시청하며 임상적 진단을 수행하게 됩니다.
            </p>
            <hr style="border: 0; height: 1px; background: #eee; margin: 20px 0;">
            <ul style="font-size: 15px; color: #555; line-height: 1.8; margin-bottom: 20px;">
                <li>영상을 주의 깊게 관찰한 후, 환자의 <b>가장 가능성 높은 질환명(진단명)</b>을 평가해 주십시오.</li>
                <li>개별 영상 평가가 끝날 때마다 시스템에 <b>실제 설계된 정답</b>이 즉시 공개됩니다.</li>
                <li>정답 확인 후, 해당 가상 환자가 질환을 얼마나 사실적으로 묘사했는지 <b>시스템의 완성도</b>를 평가해 주시면 됩니다.</li>
            </ul>
            <p style="font-size: 15px; font-weight: bold; color: #d62728; margin-bottom: 0;">
                준비가 되셨다면 아래 버튼을 눌러 첫 번째 영상 평가를 시작해 주십시오.
            </p>
        </div>
        """
        st.markdown(html_t1, unsafe_allow_html=True)
        
        st.write("")
        col1, col2, col3 = st.columns([1, 1.5, 1])
        with col2:
            # key 부여로 버튼 충돌 방지
            if st.button("Task 1 시작하기", use_container_width=True, key="btn_start_t1"):
                st.session_state.step = 'task1_phase1'
                st.rerun()
    # ---------------------------------------------------------
    # [Step] TASK 1 - Phase 1 (질환 종류 평가)
    # ---------------------------------------------------------
    elif step == 'task1_phase1':
        # [수정됨] 논리적 오류 교정: Task 1은 task1_videos 목록 사용
        video_id = st.session_state.task1_videos[st.session_state.v_idx]
        required_time = VIDEO_LENGTHS.get(video_id, 60)
        
        st.title("[Task 1] 질환 종류 진단 평가")
        st.write(f"### 임상적 진단 평가  {st.session_state.v_idx + 1} / {len(st.session_state.task1_videos)}")
        
        if f"play_started_{video_id}_p1" not in st.session_state:
            st.session_state[f"play_started_{video_id}_p1"] = False
            st.session_state[f"start_time_{video_id}_p1"] = 0
            st.session_state[f"unlocked_{video_id}_p1"] = False

        if not st.session_state[f"play_started_{video_id}_p1"]:
            if st.button("▶️ 영상 시청 시작", key=f"start_btn_{video_id}_p1"):
                st.session_state[f"play_started_{video_id}_p1"] = True
                st.session_state[f"start_time_{video_id}_p1"] = time.time()
                st.rerun()
            st.stop()
        else:
            # 1. 시청 여부 확인
            is_unlocked = st.session_state.get(f"unlocked_{video_id}_p1", False)
            
            # 2. 영상 로드 (조작 차단)
            video_path = f"videos/{video_id}.mp4"
            with open(video_path, "rb") as v_file: video_bytes = v_file.read()
            encoded_video = base64.b64encode(video_bytes).decode()
            
            # 영상 렌더링 (pointer-events: none 제거 -> 버튼 클릭 가능하게 하려면 영상 컨테이너와 버튼을 분리해야 함)
            st.markdown(f'''
                <div style="background: black;">
                    <video width="100%" autoplay playsinline style="pointer-events: none;">
                        <source src="data:video/mp4;base64,{encoded_video}" type="video/mp4">
                    </video>
                </div>
            ''', unsafe_allow_html=True)
            
            # 3. [시청 전] 로직
            if not is_unlocked:
                if f"start_time_{video_id}_p1" not in st.session_state:
                    st.session_state[f"start_time_{video_id}_p1"] = time.time()
                
                elapsed = time.time() - st.session_state[f"start_time_{video_id}_p1"]
                                
                if st.button("평가 문항 열기", key=f"unlock_btn_{video_id}_p1"):
                    if elapsed < required_time:
                        st.error(f"아직 영상 시청이 완료되지 않았습니다.")
                    else:
                        st.session_state[f"unlocked_{video_id}_p1"] = True
                        st.rerun()
                
                # 영상이 끝나기 전엔 평가 폼을 렌더링하지 않음
                st.stop()
            # 4. [시청 완료 후] - 들여쓰기를 밖으로 완전히 뺌 (if not is_unlocked 블록 외부)
            st.success("시청이 완료되었습니다. 아래 항목을 작성해 주십시오.")
            with st.form(f"survey_part1_t1_{video_id}"):
                st.markdown("**1. 이 환자의 가장 가능성 높은 질환(진단명)은 무엇이라고 생각하십니까?**")
                st.session_state.data[f"{video_id}_q10_category"] = st.selectbox(
                                "대분류 선택 (카테고리 선택)",
                                ["해당 없음", "신경발달 장애", "조현병 스펙트럼 및 기타 정신병적 장애", "양극성 및 관련 장애", "우울장애", 
                                "불안장애", "강박 및 관련 장애", "외상 및 스트레스 관련 장애", "해리 장애", "신체 증상 관련 장애", 
                                "급식 및 섭식 장애", "배설 장애", "수면-각성 장애", "성기능 부전", "성별 불쾌감", 
                                "파괴적, 충동조절 및 품행 장애", "물질관련 및 중독 장애", "신경인지 장애", "성격장애", "변태성욕 장애", "기타"],
                                index=None
                            )
                st.session_state.data[f"{video_id}_q10_detail"] = st.text_input("소분류 (세부 질환명) 기재 (※ 대분류 [해당 없음] 선택 시, ‘없음’ 기재)")

                st.session_state.data[f"{video_id}_q12_cues"] = st.multiselect("**2. 위와 같이 진단을 판단하는 데 '가장 큰 영향'을 미친 주요 단서(Cues)를 모두 선택해 주십시오. (중복 선택 가능)**", ["발화 내용", "목소리 톤 및 속도", "표정 및 시선 처리", "신체적 움직임 및 자세", "환자의 외양 및 옷차림"])
                st.session_state.data[f"{video_id}_q13_reason"] = st.text_area("**3. 위 단서를 선택한 구체적인 이유를 적어주십시오.**")

                if st.form_submit_button("평가 제출"):
                    req = [f"{video_id}_q10_category", f"{video_id}_q10_detail", f"{video_id}_q12_cues", f"{video_id}_q13_reason"]
                    if not all(st.session_state.data.get(k) for k in req): st.error("모든 평가 문항에 응답해 주십시오."); st.stop()
                
                    # if st.session_state.v_idx < len(st.session_state.task1_videos) - 1:
                    #     st.session_state.v_idx += 1
                    # else:
                    #     st.session_state.step = 'task1_intermission'
                    #     st.session_state.v_idx = 0
                    st.session_state.step = 'task1_intermission'
                    st.rerun()

    # ---------------------------------------------------------
    # [Step] TASK 1 - Intermission (정답 공개)
    # ---------------------------------------------------------
    elif step == 'task1_intermission':
        video_id = st.session_state.task1_videos[st.session_state.v_idx]
        gt_diag = GROUND_TRUTH.get(video_id, {}).get("diagnosis", "미상")

        # 상단 여백 확보
        st.markdown("<br><br>", unsafe_allow_html=True)

        # 1. 들여쓰기를 완전히 제거하여 코드 블록 인식 오류를 차단한 HTML 문자열
        html_card = f"""
<div style="background-color: #ffffff; padding: 40px; border-radius: 12px; box-shadow: 0px 8px 16px rgba(0, 0, 0, 0.05); text-align: center; border-top: 6px solid #2e7d32; border-bottom: 1px solid #eee; border-left: 1px solid #eee; border-right: 1px solid #eee;">
    <h3 style="color: #2e7d32; margin-bottom: 5px;">✅ 진단 평가 완료</h3>
    <p style="font-size: 15px; color: #777; margin-bottom: 30px;">수고하셨습니다. 방금 평가하신 가상 환자의 설계 기준을 공개합니다.</p>
    <p style="font-size: 14px; color: #555; margin-bottom: 5px; font-weight: bold;">가상 환자 설계 질환 (Ground Truth)</p>
    <h1 style="color: #1f77b4; margin: 0; font-size: 36px; font-weight: 900;">{gt_diag}</h1>
</div>
"""
        st.markdown(html_card, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # 2. st.info의 파싱 오류를 대체하는 HTML 기반의 커스텀 알림창 렌더링
        html_info = """
<div style="background-color: #e8f4f8; padding: 15px; border-radius: 8px; color: #005271; font-size: 15px; border-left: 5px solid #1f77b4;">
    💡 <b>안내:</b> 다음 단계에서는 위 공개된 <b>'설계 정답'</b>을 기준으로, 가상 환자 시스템 자체의 완성도를 평가하게 됩니다.
</div>
"""
        st.markdown(html_info, unsafe_allow_html=True)

        st.write("") 
        col1, col2, col3 = st.columns([1, 1.5, 1])
        with col2:
            if st.button("시스템 평가 시작하기", use_container_width=True):
                st.session_state.step = 'task1_phase2'
                st.rerun()

    # ---------------------------------------------------------
    # [Step] TASK 1 - Phase 2 (시스템 평가)
    # ---------------------------------------------------------
    elif step == 'task1_phase2':
        video_id = st.session_state.task1_videos[st.session_state.v_idx]
        gt_diag = GROUND_TRUTH.get(video_id, {}).get("diagnosis", "미상")

        st.title("[Task 1] 시스템 품질 및 경험 평가")
        # st.write(f"### 대상 환자 {st.session_state.v_idx + 1} / {len(st.session_state.task1_videos)}")
        st.info(f"이 가상 환자의 정답 기준: **[질환: {gt_diag}]**")
        
        # Streamlit의 st.video는 내부적으로 로컬 경로를 받아 미디어 스트리밍을 처리합니다.
        # 이 방식이 훨씬 빠르고 영상이 100% 나옵니다.
        render_system_evaluation_form(video_id, task_num=1, v_idx=st.session_state.v_idx)

    # ---------------------------------------------------------
    # [Step] TASK 2 - Instructions (Task 2 사전 안내)
    # ---------------------------------------------------------
    elif step == 'task2_instructions':
        st.title("파트 2: 우울증 심각도 평가 안내")
        st.markdown("<br>", unsafe_allow_html=True)
        
        html_t2 = """
        <div style="background-color: #ffffff; padding: 35px; border-radius: 12px; border-left: 6px solid #ff7f0e; box-shadow: 0px 4px 12px rgba(0,0,0,0.05);">
            <h3 style="color: #ff7f0e; margin-top: 0; margin-bottom: 20px;"> Task 2 진행 방식</h3>
            <p style="font-size: 16px; line-height: 1.6; color: #333;">
                수고하셨습니다. 이제 <b>두 번째 파트(Task 2)</b>가 시작됩니다.<br>
                Task 2에서 등장하는 모든 가상 환자는 <b>'주요우울장애(Major Depressive Disorder)'</b>를 앓고 있는 것으로 설정되어 있습니다.
            </p>
            <hr style="border: 0; height: 1px; background: #eee; margin: 20px 0;">
            <ul style="font-size: 15px; color: #555; line-height: 1.8; margin-bottom: 20px;">
                <li>영상을 관찰한 후, 해당 환자가 겪고 있는 우울증의 <b>증상 심각도(Severity)</b>를 평가해 주십시오.</li>
                <li>평가 직후 <b>설계된 정답(목표 심각도)</b>이 공개됩니다.</li>
                <li>정답을 기준으로 시스템이 해당 심각도를 얼마나 정확하게 표현했는지 평가해 주시면 됩니다.</li>
            </ul>
            <p style="font-size: 15px; font-weight: bold; color: #d62728; margin-bottom: 0;">
                준비가 되셨다면 아래 버튼을 눌러 Task 2 첫 번째 영상 평가를 시작해 주십시오.
            </p>
        </div>
        """
        st.markdown(html_t2, unsafe_allow_html=True)
        
        st.write("")
        col1, col2, col3 = st.columns([1, 1.5, 1])
        with col2:
            if st.button("Task 2 시작하기", use_container_width=True, key="btn_start_t2"):
                st.session_state.step = 'task2_phase1'
                st.rerun()
    # ---------------------------------------------------------
    # [Step] TASK 2 - Phase 1 (우울증 심각도 평가)
    # ---------------------------------------------------------
    elif step == 'task2_phase1':
        # [수정됨] 논리적 오류 교정: Task 2는 task2_videos 목록 사용
        video_id = st.session_state.task2_videos[st.session_state.v_idx]
        required_time = VIDEO_LENGTHS.get(video_id, 60)
        
        st.title("[Task 2] 우울증 심각도 평가")
        st.write(f"### 임상적 증상 평가  {st.session_state.v_idx + 1} / {len(st.session_state.task2_videos)}")
        
        if f"play_started_{video_id}_p1" not in st.session_state:
            st.session_state[f"play_started_{video_id}_p1"] = False
            st.session_state[f"start_time_{video_id}_p1"] = 0
            st.session_state[f"unlocked_{video_id}_p1"] = False

        if not st.session_state[f"play_started_{video_id}_p1"]:
            if st.button("▶️ 영상 시청 시작", key=f"start_btn_{video_id}_p1"):
                st.session_state[f"play_started_{video_id}_p1"] = True
                st.session_state[f"start_time_{video_id}_p1"] = time.time()
                st.rerun()
            st.stop()
        else:
            # 1. 시청 여부 확인
            is_unlocked = st.session_state.get(f"unlocked_{video_id}_p2", False)
            
            # 2. 영상 로드 (조작 차단)
            video_path = f"videos/{video_id}.mp4"
            with open(video_path, "rb") as v_file: video_bytes = v_file.read()
            encoded_video = base64.b64encode(video_bytes).decode()
            
            # 영상 렌더링 (pointer-events: none으로 마우스 조작 차단)
            st.markdown(f'''
                <div style="background: black;">
                    <video width="100%" autoplay playsinline style="pointer-events: none;">
                        <source src="data:video/mp4;base64,{encoded_video}" type="video/mp4">
                    </video>
                </div>
            ''', unsafe_allow_html=True)
            
            # 3. [시청 전] 로직
            if not is_unlocked:
                if f"start_time_{video_id}_p2" not in st.session_state:
                    st.session_state[f"start_time_{video_id}_p2"] = time.time()
                
                elapsed = time.time() - st.session_state[f"start_time_{video_id}_p2"]

                if st.button("평가 문항 열기", key=f"unlock_btn_{video_id}_p2"):
                    if elapsed < required_time:
                        st.error(f"아직 영상 시청이 완료되지 않았습니다. ({int(elapsed)}/{required_time}초)")
                    else:
                        st.session_state[f"unlocked_{video_id}_p2"] = True
                        st.rerun()
            
                # 영상 완료 전에는 평가 폼을 렌더링하지 않음
                st.stop()
            # 4. [시청 완료 후] - 들여쓰기를 밖으로 완전히 뺌 (if not is_unlocked 블록 외부)
            st.success("시청이 완료되었습니다. 아래 항목을 작성해 주십시오.")

            with st.form(f"survey_part1_t2_{video_id}"):
                st.write("**[안내] 이 가상 환자의 질환은 '주요우울장애(Major Depressive Disorder)'입니다. 해당 질환을 바탕으로 환자의 증상 심각도를 평가해 주십시오.**")
                
                st.session_state.data[f"{video_id}_q11_severity"] = st.radio("**1. 이 환자의 전반적인 증상 심각도(Severity)는 어느 정도라고 평가하십니까?**", ["None (증상 없음)", "Mild (경도)", "Moderate (중등도)", "Severe (중증)"], index=None)
                st.session_state.data[f"{video_id}_q12_cues"] = st.multiselect("**2. 위와 같이 심각도를 판단하는 데 '가장 큰 영향'을 미친 주요 단서(Cues)를 모두 선택해 주십시오.**", ["발화 내용", "목소리 톤 및 속도", "표정 및 시선 처리", "신체적 움직임 및 자세", "환자의 외양 및 옷차림"])
                st.session_state.data[f"{video_id}_q13_reason"] = st.text_area("**3. 위 단서를 선택한 구체적인 이유를 적어주십시오.**")

                if st.form_submit_button("평가 제출"):
                    req = [f"{video_id}_q11_severity", f"{video_id}_q12_cues", f"{video_id}_q13_reason"]
                    if not all(st.session_state.data.get(k) for k in req): st.error("모든 평가 문항에 응답해 주십시오."); st.stop()
                    
                    st.session_state.step = 'task2_intermission'
                    st.rerun()

    # ---------------------------------------------------------
    # [Step] TASK 2 - Intermission (정답 공개)
    # ---------------------------------------------------------
    elif step == 'task2_intermission':
        video_id = st.session_state.task2_videos[st.session_state.v_idx]
        
        # 팩트 체크 1: 질환명과 심각도(Severity)를 모두 가져옵니다.
        gt_diag = GROUND_TRUTH.get(video_id, {}).get("diagnosis", "주요우울장애")
        gt_sev = GROUND_TRUTH.get(video_id, {}).get("severity", "미상")

        st.markdown("<br><br>", unsafe_allow_html=True)

        # 팩트 체크 2: Task 2의 목적에 맞게 워딩 수정 및 심각도 시각적 강조
        html_card = f"""
<div style="background-color: #ffffff; padding: 40px; border-radius: 12px; box-shadow: 0px 8px 16px rgba(0, 0, 0, 0.05); text-align: center; border-top: 6px solid #2e7d32; border-bottom: 1px solid #eee; border-left: 1px solid #eee; border-right: 1px solid #eee;">
<h3 style="color: #2e7d32; margin-bottom: 5px;">✅ 심각도 평가 완료</h3>
<p style="font-size: 15px; color: #777; margin-bottom: 30px;">수고하셨습니다. 방금 평가하신 가상 환자의 설계 기준을 공개합니다.</p>
<p style="font-size: 14px; color: #555; margin-bottom: 5px; font-weight: bold;">가상 환자 설계 기준 (Ground Truth)</p>
<h3 style="color: #555; margin: 0; font-size: 20px; font-weight: normal;">질환: {gt_diag}</h3>
<h1 style="color: #d62728; margin: 10px 0 0 0; font-size: 40px; font-weight: 900;">심각도: {gt_sev}</h1>
</div>
"""
        st.markdown(html_card, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        html_info = """
<div style="background-color: #e8f4f8; padding: 15px; border-radius: 8px; color: #005271; font-size: 15px; border-left: 5px solid #1f77b4;">
    💡 <b>안내:</b> 다음 단계에서는 위 공개된 <b>'설계 정답'</b>을 기준으로, 가상 환자 시스템 자체의 완성도를 평가하게 됩니다.
</div>
"""
        st.markdown(html_info, unsafe_allow_html=True)

        st.write("") 
        col1, col2, col3 = st.columns([1, 1.5, 1])
        with col2:
            # 팩트 체크 3: Task 2 루프의 독립성을 위해 key를 부여하고 이동 경로를 교정
            if st.button("시스템 평가 시작하기", use_container_width=True, key=f"sys_eval_t2_{video_id}"):
                st.session_state.step = 'task2_phase2' 
                st.rerun()
    # ---------------------------------------------------------
    # [Step] TASK 2 - Phase 2 (시스템 평가)
    # ---------------------------------------------------------
    elif step == 'task2_phase2':
        video_id = st.session_state.task2_videos[st.session_state.v_idx]
        gt_diag = GROUND_TRUTH.get(video_id, {}).get("diagnosis", "미상")
        gt_sev = GROUND_TRUTH.get(video_id, {}).get("severity", "미상")

        st.title("[Task 2] 시스템 품질 및 경험 평가")
        #st.write(f"### 대상 환자 {st.session_state.v_idx + 1} / {len(st.session_state.task2_videos)}")
        st.info(f"이 가상 환자의 정답 기준: **[질환: {gt_diag} / 심각도: {gt_sev}]**")

        render_system_evaluation_form(video_id, task_num=2, v_idx=st.session_state.v_idx)

    # ---------------------------------------------------------
    # [Step] Final (종합 평가)
    # ---------------------------------------------------------
    elif step == 'final':
        st.title("가상환자 평가 실험 완료")
        st.subheader("임상 훈련 도구로서의 활용성 및 종합 평가")
        st.info("모든 영상 평가가 완료되었습니다. 마지막으로 본 가상 환자 시스템 전체에 대한 종합적인 의견을 여쭙습니다.")
        
        with st.form("final_comprehensive_survey"):
            st.session_state.data["q29_overall_exp"] = st.radio("**1. 가상 환자를 사용한 귀하의 전반적인 경험을 1에서 10까지의 척도로 평가해 주십시오. (1점은 '매우 나쁨', 10점은 '매우 좋음)**", [str(i) for i in range(1, 11)], index=None, horizontal=True)
            st.session_state.data["q30_reuse_intent"] = st.radio("**2. 향후 훈련 과정 중에 가상 환자를 다시 사용할 의향이 얼마나 있습니까? (1점은 '전혀 관심이 없음', 10점은 '매우 관심이 있음')**", [str(i) for i in range(1, 11)], index=None, horizontal=True)
            st.session_state.data["q31_pros"] = st.text_area("**3. 임상 교육 도구로서 본 가상 환자 시스템의 가장 큰 장점은 무엇이라고 생각하십니까?**")
            st.session_state.data["q32_cons"] = st.text_area("**4. 본 가상 환자 시스템에서 이질감을 느꼈던 부분이나 개선되어야 할 점이 있다면 제안해 주십시오.**")
            st.session_state.data["q33_diff_diagnosis"] = st.text_area("**5. 어떤 질환의 감별이 가장 어려웠습니까?**")
            if st.form_submit_button("최종 데이터 제출 및 실험 종료"):
                if not all([st.session_state.data.get("q29_overall_exp"), st.session_state.data.get("q30_reuse_intent"), st.session_state.data.get("q31_pros"), st.session_state.data.get("q32_cons"), st.session_state.data.get("q33_diff_diagnosis")]):
                    st.error("모든 종합 평가 문항을 작성해 주십시오.")
                    st.stop()
                st.session_state.step = 'save'
                st.rerun()

    # ---------------------------------------------------------
    # [Step] Save Data
    # ---------------------------------------------------------
    # ---------------------------------------------------------
    # [Step] Save Data (데이터 및 타임로그 분리 저장)
    # ---------------------------------------------------------
    elif step == 'save':
        with st.spinner("데이터를 서버에 기록 중입니다. 잠시만 기다려주세요..."):
            client = get_gspread_client()
            db = client.open("ExperimentDB")
            
            # 팩트 체크: 두 개의 독립된 워크시트 객체 호출
            sheet_data = db.worksheet("logs")
            sheet_time = db.worksheet("time_logs") 
            
            # 공통 식별자(Primary Key) 생성
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.session_state.data['timestamp'] = current_time
            st.session_state.time_logs['timestamp'] = current_time
            st.session_state.time_logs['name'] = st.session_state.data.get('name', 'Unknown')
            
            # 전체 실험 총 소요 시간 계산
            st.session_state.time_logs["Experiment_Total_Time"] = round(time.time() - st.session_state.global_start_time, 2)
            
            st.session_state.data['task1_order'] = ", ".join(st.session_state.task1_videos)
            st.session_state.data['task2_order'] = ", ".join(st.session_state.task2_videos)
            
            all_videos = ["None_T1", "OCD", "MDD", "GAD", "None_T2", "Mild", "Moderate", "Severe"]

            # ==========================================
            # [파이프라인 1] 설문 응답 전용 데이터 (logs 시트용)
            # ==========================================
            # 팩트 체크: 새롭게 추가/수정된 demography 변수 100% 반영
            ordered_keys_data = [
                'timestamp', 'name', 'gender', 'birth_date', 'major', 'degree', 
                'simulation_experience', 'clinical_experience', 'consultation_count', 
                'clinical_years', 'consulted_disorders', 'certifications', 
                'communication_difficulty', 'dsm_familiarity', 'diag_confidence', 
                'important_cue_1', 'important_cue_2', 'important_cue_3', 
                'important_cue_other', 'frequent_disorders', 'confident_disorders', 
                'group_id', 'task1_order', 'task2_order'
            ]
            
            for v in all_videos:
                if v in st.session_state.task2_videos:
                    st.session_state.data[f"{v}_q10_category"] = "우울장애 (사전제공)"
                    st.session_state.data[f"{v}_q10_detail"] = "MDD (사전제공)"
                else: 
                    st.session_state.data[f"{v}_q11_severity"] = "N/A" 
                
                ordered_keys_data.extend([
                    f"{v}_q10_category", f"{v}_q10_detail", f"{v}_q11_severity",
                    f"{v}_q12_cues", f"{v}_q13_reason",
                    f"{v}_q14_humanlikeness", f"{v}_q15_naturalness", f"{v}_q16_fluency",
                    f"{v}_q17_realism", f"{v}_q18_consistency", f"{v}_q19_cognitive",
                    f"{v}_q20_seen_similar", f"{v}_q21_frequency", f"{v}_q22_common",
                    f"{v}_q23_reasoning1", f"{v}_q24_reasoning2", f"{v}_q25_reasoning4",
                    f"{v}_q26_learning1", f"{v}_q27_learning2", f"{v}_q28_overall_case",
                    f"{v}_feedback_pros", f"{v}_feedback_cons"
                ])
                
            ordered_keys_data.extend(["q29_overall_exp", "q30_reuse_intent", "q31_pros", "q32_cons", "q33_diff_diagnosis"])
            
            ordered_data_row = []
            for k in ordered_keys_data:
                val = st.session_state.data.get(k, "N/A")
                if isinstance(val, list): val = ", ".join(map(str, val))
                ordered_data_row.append(val)

            # ==========================================
            # [파이프라인 2] 타임 로그 전용 데이터 (time_logs 시트용)
            # ==========================================
            ordered_keys_time = ['timestamp', 'name', 'Experiment_Total_Time', 'Task1_Total_Time', 'Task2_Total_Time']
            
            for v in all_videos:
                t_prefix = "Task2" if v in st.session_state.task2_videos else "Task1"
                ordered_keys_time.extend([
                    f"{t_prefix}_{v}_Video_Time",
                    f"{t_prefix}_{v}_Phase1_Survey_Time",
                    f"{t_prefix}_{v}_Phase2_Time"
                ])
                
            ordered_time_row = []
            for k in ordered_keys_time:
                val = st.session_state.time_logs.get(k, 0.0) 
                ordered_time_row.append(val)

            # ==========================================
            # [API 전송] 두 개의 독립된 시트에 각각 append_row 실행
            # ==========================================
            sheet_data.append_row(ordered_data_row)
            sheet_time.append_row(ordered_time_row)
            
            st.session_state.step = 'done'
            st.rerun()

    elif step == 'done':
        st.balloons()
        st.success("설문이 모두 완료되었습니다. 연구에 참여해 주셔서 진심으로 감사드립니다.")
        st.write("안전하게 창을 닫아주셔도 좋습니다.")

def render_system_evaluation_form(video_id, task_num, v_idx):
    with st.form(f"survey_part2_{video_id}_{v_idx}"):
        st.write("*아래의 모든 평가 기준은 **실제 정답값**을 바탕으로 합니다.*")
        st.subheader("가상 환자의 시각/언어적 자연스러움 평가")
                        
        st.session_state.data[f"{video_id}_q14_humanlikeness"] = st.radio(
            "**1. 가상 환자는 인간 상호작용에서 흔히 볼 수 있는 특성을 보였습니까, 아니면 자동적인 존재처럼 보였습니까?**",
            [
                "1점 - 인간과 닮지 않음 (감정적 미묘함, 상황 인식 및 자발성이 부족하여 일관되게 인위적인 모습을 보입니다.)",
                "2점 - 약간 인간과 유사함 (종종 기계적인 느낌을 주며, 경직된 패턴, 반복적인 표현, 부자연스러운 반응을 보입니다.)",
                "3점 - 다소 인간과 유사함 (인간과 유사한 경향을 보이지만, 때때로 정해진 각본대로 행동하거나 자연스러운 행동 변화가 부족해 보입니다.)",
                "4점 - 대체로 인간과 유사함 (감정 표현이나 반응 패턴에 약간의 불일치가 있을 뿐, 전반적으로 인간과 유사한 방식으로 행동합니다.)",
                "5점 - 매우 인간과 유사함 (실제 인간에게서 볼 수 있는 풍부하고 미묘한 뉘앙스와 예측 불가능한 행동을 보입니다. 반응에는 감정, 미묘한 어조 변화, 적절한 망설임이 포함됩니다.)"
            ], index=None
        )
        
        st.session_state.data[f"{video_id}_q15_naturalness"] = st.radio(
            "**2. 가상 환자의 의사소통 행동이 실제 사람들의 행동과 일치했습니까?**",
            [
                "1점 - 매우 부자연스러움 (기계적이고 부자연스럽거나 상황에 맞지 않는 방식으로 의사소통하여 상호작용이 인위적으로 느껴집니다.)",
                "2점 - 다소 부자연스러움 (대화가 부자연스럽고, 로봇 같거나, 지나치게 대본처럼 느껴져 현실감이 떨어집니다.)",
                "3점 - 보통 (환자의 말 흐름은 적절하지만, 때때로 경직되거나 지나치게 격식적인 언어를 사용하여 자연스러움이 떨어집니다.)",
                "4점 - 대체로 자연스러움 (대체로 현실적인 방식으로 의사소통하며, 부자연스러운 표현이나 상호작용은 가끔씩만 나타납니다.)",
                "5점 - 매우 자연스러움 (의사소통 방식, 어조 및 표현이 실제 사람 상호작용과 완벽하게 일치합니다. 다양한 대화 신호에 자연스럽게 적응합니다.)"
            ], index=None
        )
        
        st.session_state.data[f"{video_id}_q16_fluency"] = st.radio(
            "**3. 가상 환자가 일관성 있고 매끄러운 방식으로 의사소통을 했습니까?**",
            [
                "1점 - 전혀 유창하지 않음 (논리적 일관성에 어려움을 겪으며, 자주 단절되거나 불완전하거나 무의미한 답변을 합니다.)",
                "2점 - 다소 유창하지 않음 (잦은 머뭇거림, 부자연스러운 멈춤 또는 단절된 답변으로 인해 의사소통이 방해받습니다.)",
                "3점 - 보통 (일부 답변이 단편적이거나 약간 어색하지만 대체로 이해할 수 있습니다.)",
                "4점 - 대체로 유창함 (답변은 일반적으로 매끄럽고 구조가 잘 잡혀 있으며, 일관성이나 흐름에 있어 사소한 불일치만 있을 뿐입니다.)",
                "5점 - 매우 유창함 (최소한의 멈춤, 갑작스러운 주제 전환 또는 일관성 부족 없이 일관성 있고 구체적이며 매끄러운 방식으로 의사소통합니다.)"
            ], index=None
        )

        st.divider()
        st.subheader("가상 환자의 임상적 현실성 평가")
        st.write("*가상 환자가 실제 환자의 특성을 얼마나 잘 반영했는지 1~5점 척도로 평가해 주십시오.*")

        st.session_state.data[f"{video_id}_q17_realism"] = st.radio(
            "**4. 가상 환자가 할당된 질환/심각도과 일치하는 방식으로 증상을 보였습니까?**",
            [
                "1점 - 전혀 현실적이지 않음 (질환과 관련 없는 증상을 나타내거나 현실적인 증상이 없습니다.)",
                "2점 - 다소 비현실적임 (증상이 종종 불완전하거나, 잘못 표현되거나, 피상적으로 표현됩니다.)",
                "3점 - 보통 (일부 증상은 임상적 기대치와 일치하지만, 다른 증상은 과장되거나, 나타나지 않거나 일관성이 없습니다.)",
                "4점 - 대체로 현실적임 (대부분의 증상이 정확하게 표현되었으며, 사소한 부정확함이나 세부 정보 누락만 있을 뿐입니다.)",
                "5점 - 매우 현실적임 (광범위한 질환 관련 증상을 정확하게 나타냅니다.)"
            ], index=None
        )
        
        st.session_state.data[f"{video_id}_q18_consistency"] = st.radio(
            "**5. 가상 환자가 할당된 질환/심각도에 맞춰 감정적, 인지적 패턴을 일관되게 유지했습니까?**",
            [
                "1점 - 전혀 일관되지 않음 (환자의 감정 표현이 무작위적이거나 모순되어 신뢰성이 떨어집니다.)",
                "2점 - 다소 일관되지 않음 (감정적 반응의 잦은 불일치는 질환/심각도 수준의 변동과 같은 현실성을 감소시킵니다.)",
                "3점 - 보통 (때때로 질환/심각도와 일치하지만 가끔 강도나 적절성이 달라지기도 합니다.)",
                "4점 - 대체로 일관됨 (일반적으로 적절한 감정적 반응을 유지하지만 사소한 편차나 불일치가 있습니다.)",
                "5점 - 매우 일관됨 (상호작용 내내 일치하는 안정적인 감정적, 인지적 패턴을 유지합니다.)"
            ], index=None
        )
        
        st.session_state.data[f"{video_id}_q19_cognitive"] = st.radio(
            "**6. 가상 환자의 발화가 할당된 질환과 관련된 인지 처리 패턴을 잘 반영했습니까?**",
            [
                "1점 - 전혀 반영하지 않음 (질환과 관련된 의미 있는 인지 처리 패턴을 전혀 나타내지 않아 신뢰성이 떨어집니다.)",
                "2점 - 다소 반영하지 않음 (인지 패턴이 약하게 표현되거나 때로는 알려진 질환 특성과 모순됩니다.)",
                "3점 - 보통 (일부 질환과 관련된 인지 특성이 존재하지만 일관성 있게 표현되지 않거나 항상 일치하지는 않습니다.)",
                "4점 - 대체로 반영함 (일반적으로 적절한 인지 처리 패턴을 보이지만 약간의 불일치가 있습니다.)",
                "5점 - 매우 정확히 반영함 (임상적으로 타당하고 일관된 방식으로 질환과 관련된 인지 패턴을 보여줍니다.)"
            ], index=None
        )

        st.divider()
        st.subheader("가상 환자 경험에 대한 조사")
        st.write("*이러한 유형의 가상 환자를 다뤄본 경험에 대해 답해 주십시오.*")
        st.session_state.data[f"{video_id}_q20_seen_similar"] = st.radio("**7. 위 가상 환자와 유사한 환자를 만나본 적이 있습니까?**", ["예", "아니요"], index=None, horizontal=True)
        st.session_state.data[f"{video_id}_q21_frequency"] = st.radio("**8. 위 가상 환자와 같은 환자를 얼마나 자주 만나십니까?**", ["거의 매일", "일주일에 여러 번", "한 달에 한두 번", "일 년에 한두 번", "만난 적 없음"], index=None)
        st.session_state.data[f"{video_id}_q22_common"] = st.radio("**9. 위 가상 환자와 같은 환자는 실제 임상 현장에서 흔히 볼 수 있습니다.**", ["1점 (전혀 동의하지 않음)", "2점 (동의하지 않음)", "3점 (보통)", "4점 (동의함)", "5점 (매우 동의함)"], index=None, horizontal=True)

        st.divider()
        st.subheader("가상 환자 경험 평가")
        st.write("*이 설문지는 학생들이 가상 환자를 활용한 경험, 특히 임상 추론 능력의 발달과 관련된 경험을 평가하기 위한 것입니다.*")

        likert_scales = ["1점 (전혀 동의하지 않음)", "2점 (동의하지 않음)", "3점 (보통/중립)", "4점 (동의함)", "5점 (매우 동의함)"]
        
        st.markdown("**[상담에서의 전문적인 접근 방식]**")
        st.session_state.data[f"{video_id}_q23_reasoning1"] = st.radio("**10. 환자의 문제를 특징짓기 위해 필요한 정보를 수집하는 데 적극적으로 참여했다.**", likert_scales, index=None, horizontal=True)
        st.session_state.data[f"{video_id}_q24_reasoning2"] = st.radio("**11. 새로운 정보가 주어짐에 따라 초기 인상(가설)을 수정하는 데 적극적으로 참여했다.**", likert_scales, index=None, horizontal=True)
        st.session_state.data[f"{video_id}_q25_reasoning4"] = st.radio("**12. 관찰된 소견들이 감별 진단들을 각각 지지하는지 혹은 반박하는지 고민하는 데 적극적으로 참여했다.**", likert_scales, index=None, horizontal=True)

        st.markdown("**[상담의 학습 효과]**")
        st.session_state.data[f"{video_id}_q26_learning1"] = st.radio("**13. 실제 환자를 만났을 때 진단을 확정하고 감별해 낼 준비가 더 잘 되었다고 느낀다.**", likert_scales, index=None, horizontal=True)
        st.session_state.data[f"{video_id}_q27_learning2"] = st.radio("**14. 실제 환자를 돌볼 준비가 더 잘 되었다고 느낀다.**", likert_scales, index=None, horizontal=True)
        st.markdown("**[사례 평가에 대한 종합적인 판단]**")
        st.session_state.data[f"{video_id}_q28_overall_case"] = st.radio("**15. 전반적으로 가치 있는 학습 경험이었다.**", likert_scales, index=None, horizontal=True)

        # [추가됨] 가상 환자 추가 피드백 (영상별)
        st.divider()
        st.subheader("가상 환자 추가 피드백")
        st.markdown("**16. 영상 속 가상 환자의 표정, 행동, 발화 내용, 음성 등에 대해 긍정적인 부분과 개선이 필요한 부분을 작성해 주십시오.**")
        st.write("*(설계된 정답 질환의 특성을 잘 살린 부분이나, 반대로 더 현실감을 높이기 위해 수정해야 할 점을 구체적으로 적어주십시오.)*")
        
        st.session_state.data[f"{video_id}_feedback_pros"] = st.text_area("좋았던 점 (Strengths)", placeholder="가상 환자의 긍정적인 부분이나 현실적이었던 점을 적어주세요.")
        st.session_state.data[f"{video_id}_feedback_cons"] = st.text_area("부족했던 점 (Weaknesses)", placeholder="개선이 필요한 부분이나 부자연스러웠던 점을 적어주세요.")

        if st.form_submit_button("평가 제출"):
            # 1. 객관식 문항 검증
            req_part2 = [f"{video_id}_q{i}_{name}" for i, name in zip(range(14, 29), ['humanlikeness', 'naturalness', 'fluency', 'realism', 'consistency', 'cognitive', 'seen_similar', 'frequency', 'common', 'reasoning1', 'reasoning2', 'reasoning4', 'learning1', 'learning2', 'overall_case'])]
            if not all(st.session_state.data.get(k) for k in req_part2): st.error("모든 객관식 평가 항목에 응답해 주십시오."); st.stop()
            # 2. 주관식 문항 검증
            if not st.session_state.data.get(f"{video_id}_feedback_pros") or not st.session_state.data.get(f"{video_id}_feedback_cons"):
                st.error("좋았던 점과 부족했던 점을 모두 작성해 주십시오.")
                st.stop()

            if task_num == 1:
                    if st.session_state.v_idx < len(st.session_state.task1_videos) - 1:
                        st.session_state.v_idx += 1
                        st.session_state.step = 'task1_phase1' # 다음 Task 1 영상으로 복귀
                    else:
                        st.session_state.v_idx = 0
                        st.session_state.step = 'task2_instructions' # Task 1 종료 시 Task 2 안내로 이동
                    st.rerun()
                
            elif task_num == 2:
                if st.session_state.v_idx < len(st.session_state.task2_videos) - 1:
                    st.session_state.v_idx += 1
                    st.session_state.step = 'task2_phase1' # 다음 Task 2 영상으로 복귀
                else:
                    # Task 2까지 모두 종료된 경우의 처리
                    st.session_state.v_idx = 0
                    st.session_state.step = 'final' # 최종 완료 페이지로 이동
                st.rerun()

if __name__ == "__main__":
    main()