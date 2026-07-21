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
import uuid

# [설정] 구글 시트 클라이언트
def get_gspread_client():
    creds_dict = json.loads(st.secrets["gcp_service_account"])
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)
def save_intermediate_data(current_step):
    """
    현재까지 st.session_state.data와 st.session_state.time_logs에 모인 
    모든 정보를 시트에 기록(존재 시 Update, 없을 시 Append)합니다.
    """
    try:
        # 1. 구글 시트 연결
        client = get_gspread_client()
        db = client.open("ExperimentDB")
        sheet_data = db.worksheet("logs")
        sheet_time = db.worksheet("time_logs")


        # 2. 공통 식별자 및 타임스탬프 갱신
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state.data['session_id'] = st.session_state.session_id
        st.session_state.data['timestamp'] = current_time
        if "started_at" not in st.session_state.data:
            st.session_state.data["started_at"] = current_time

        st.session_state.data["last_saved_at"] = current_time
        
        st.session_state.time_logs['session_id'] = st.session_state.session_id
        st.session_state.time_logs['timestamp'] = current_time
        if "started_at" not in st.session_state.time_logs:
            st.session_state.time_logs["started_at"] = current_time

        st.session_state.time_logs["last_saved_at"] = current_time
        
        st.session_state.time_logs['name'] = st.session_state.data.get('name', 'Unknown')
        
        if 'global_start_time' in st.session_state:
            st.session_state.time_logs["Experiment_Total_Time"] = round(time.time() - st.session_state.global_start_time, 2)

        # 3. Task 진행 순서 배열 문자열 처리 (아직 안 섞였을 수도 있으므로 예외 처리)
        st.session_state.data['task1_order'] = ", ".join(st.session_state.get('task1_videos', []))
        st.session_state.data['task2_order'] = ", ".join(st.session_state.get('task2_videos', []))

        all_videos = ["None_T1", "OCD", "MDD", "GAD", "None_T2", "Mild", "Moderate", "Severe"]

        # ==========================================
        # [데이터 준비] logs 시트용 (session_id를 맨 앞에 추가)
        # ==========================================
        ordered_keys_data = [
            'session_id', 'timestamp', 'started_at', 'last_saved_at', 'name', 'gender', 'birth_date', 'major', 
            'certifications', 'clinical_experience', 'consulted_disorders', 'clinical_years', 
            'communication_difficulty', 'cue_rank_1', 'cue_rank_2', 'cue_rank_3', 'cue_rank_4', 'cue_rank_5',
            'simulation_experience', 'group_id', 'task1_order', 'task2_order'
        ]
        
        for v in all_videos:
            task1_vids = st.session_state.get('task1_videos', [])
            
            # [논리 교정] Task 1(심각도 평가) 영상인 경우, 진단명은 '주요우울장애'로 고정
            if v in task1_vids:
                st.session_state.data[f"{v}_q10_category"] = "주요우울장애 (사전제공)"
            
            # [논리 교정] Task 2(진단 평가) 영상인 경우, 심각도 응답이 없으므로 N/A 처리
            elif f"{v}_q11_severity" not in st.session_state.data: 
                st.session_state.data[f"{v}_q11_severity"] = "N/A"
            
            ordered_keys_data.extend([
                f"{v}_q10_category", f"{v}_q11_severity",
                f"{v}_q12_cues", f"{v}_q13_reason",
                f"{v}_q14_humanlikeness", f"{v}_q15_naturalness", f"{v}_q16_fluency",
                f"{v}_q17_realism", f"{v}_q18_consistency", f"{v}_q19_cognitive",
                f"{v}_q20_reasoning1", f"{v}_q21_reasoning2", f"{v}_q22_reasoning4",
                f"{v}_q23_learning1", f"{v}_q24_learning2", f"{v}_q25_overall_case",
                f"{v}_feedback_pros", f"{v}_feedback_cons"
            ])
            
        ordered_keys_data.extend(["q26_overall_exp", "q27_reuse_intent", "q28_diff_diagnosis", "q29_pros", "q30_cons",])
        ordered_data_row = []
        for k in ordered_keys_data:
            val = st.session_state.data.get(k, "N/A")
            if isinstance(val, list): val = ", ".join(map(str, val))
            ordered_data_row.append(val)

        # ==========================================
        # [데이터 준비] time_logs 시트용 (session_id를 맨 앞에 추가)
        # ==========================================
        ordered_keys_time = ['session_id', 'timestamp', 'started_at', 'last_saved_at', 'name', 'Experiment_Total_Time', 'Task1_Total_Time', 'Task2_Total_Time']
        task1_fixed_vids = ["None_T1", "Mild", "Moderate", "Severe"]

        for v in all_videos:
            # 영상 이름표가 Task 1에 속하면 무조건 "Task1", 아니면 "Task2"로 강제 고정
            t_prefix = "Task1" if v in task1_fixed_vids else "Task2"
            ordered_keys_time.extend([
                f"{t_prefix}_{v}_Video_Time",
                f"{t_prefix}_{v}_Phase1_Survey_Time",
                f"{t_prefix}_{v}_Phase2_Time"
            ])
            
        ordered_time_row = []
        for k in ordered_keys_time:
            # session_id, timestamp, name 등의 문자열을 안전하게 처리하기 위한 분기
            if k in ['session_id', 'timestamp', 'name']:
                val = st.session_state.time_logs.get(k, "N/A")
            else:
                val = st.session_state.time_logs.get(k, 0.0) 
            ordered_time_row.append(val)

        # ==========================================
        # 4. 시트 덮어쓰기 (Upsert: 존재하면 Update, 없으면 Append)
        # ==========================================
        
        # logs 시트 처리
        col1_data = sheet_data.col_values(1) # A열 (session_id) 가져오기
        if st.session_state.session_id in col1_data:
            # 1-based index (header가 1이므로 +1)
            row_idx = col1_data.index(st.session_state.session_id) + 1
            sheet_data.update(f"A{row_idx}", [ordered_data_row])
        else:
            sheet_data.append_row(ordered_data_row)

        # time_logs 시트 처리
        col1_time = sheet_time.col_values(1)
        if st.session_state.session_id in col1_time:
            row_idx = col1_time.index(st.session_state.session_id) + 1
            sheet_time.update(f"A{row_idx}", [ordered_time_row])
        else:
            sheet_time.append_row(ordered_time_row)
            
        print(f"[DEBUG] {current_step} 단계 데이터 시트 저장 완료 (ID: {st.session_state.session_id})")
        return True 
    except Exception as e:
        # 백그라운드 저장 중 오류가 발생하더라도 사용자 실험 화면은 중단되지 않게 통과시킴
        print(f"[ERROR] {current_step} 단계 시트 저장 실패: {e}")
        return False

# [수정됨] 엑셀 스키마 보호를 위해 ID 분리 (None_T2: 정상 진단용 / None_T1: 심각도 없음용)
GROUPS = {
    "group_A": {
        "task1": ["None_T1", "Mild", "Moderate", "Severe"], # 우울증 심각도 평가용 4개
        "task2": ["None_T2", "OCD", "GAD", "MDD"]  # 질환 종류 맞추기용 4개
    }
}

# [수정됨] 분리된 ID 반영
GROUND_TRUTH = {
    "None_T1": {"diagnosis": "질환 없음", "severity": "None"},
    "None_T2": {"diagnosis": "질환 없음", "severity": "N/A"},
    "Mild": {"diagnosis": "Major Depressive Disorder", "severity": "Mild"},
    "Moderate": {"diagnosis": "Major Depressive Disorder", "severity": "Moderate"},
    "Severe": {"diagnosis": "Major Depressive Disorder", "severity": "Severe"},
    "OCD": {"diagnosis": "Obsessive-Compulsive Disorder", "severity": "N/A"},
    "GAD": {"diagnosis": "Generalized Anxiety Disorder", "severity": "N/A"},
    "MDD": {"diagnosis": "Major Depressive Disorder", "severity": "N/A"}
}

# VIDEO_LENGTHS = {
#     "None_T1": 226, "Mild": 230, "Moderate": 250, "Severe": 208,
#     "None_T2": 235, "OCD": 344, "GAD": 265, "MDD": 185
# }
VIDEO_LENGTHS = {
    "None_T1": 5, "Mild": 5, "Moderate": 5, "Severe": 5,
    "None_T2": 5, "OCD": 5, "GAD": 5, "MDD": 5
}
# 실험 전체 및 타임 로그 관리를 위한 독립 세션 초기화
if 'time_logs' not in st.session_state:
    st.session_state.time_logs = {}
def main():
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
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    
    if 'global_start_time' not in st.session_state:
        st.session_state.global_start_time = time.time()

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
                <li><b> [Part 1]</b> 환자의 동일한 질환에 대해 <b>심각도</b>를 평가하는 과제</li>
                <li><b> [Part 2]</b> 환자의 <b>질환 종류</b>를 진단하는 과제</li>
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
        
        data = st.session_state.data # 기존에 입력한 값이 있으면 위젯에 유지하도록

        with st.form("demography"):
            st.markdown("**[기본 인적 사항]**")
            st.session_state.data['name'] = st.text_input("**1. 성함**")
            st.session_state.data['gender'] = st.radio("**2. 성별**", options=["남성", "여성"], index=None, horizontal=True)
            st.session_state.data['birth_date'] = st.text_input("**3. 생년월일 (예: 010101)**", max_chars=6)
            st.session_state.data['major'] = st.text_input("**4. 전공 분야 (예: 의학과, 간호학과, 심리학과 등)**")
            st.session_state.data['certifications'] = st.text_area(
                "**5. 보유하고 있는 상담 및 정신의학 자격증 이름 전체 기재**",
                placeholder="※ 정확한 명칭과 급수를 기재해 주십시오. (예: 정신건강임상심리사 1급, 청소년상담사 2급)\n※ 해당 사항이 없을 경우 '없음'이라고 기재해 주십시오."
            )
            st.divider()
            st.markdown("**[임상 및 훈련 경험]**")
            st.session_state.data['clinical_experience'] = st.radio(
                "**6. 실제 정신건강 환자를 직접 상담(면담)한 경험이 있습니까?**", 
                options=["없음", "1~5회", "6~10회", "10~30회", "30회 이상"], 
                index=None, horizontal=True
            )
            st.session_state.data['consulted_disorders'] = st.text_input("**6.1 실제 면담 경험이 있다면, 어떤 질환의 환자를 면담해 보셨나요?(※ 6. 상담 경험 없는 응답자일 시 '없음' 기재)**")
            st.session_state.data['clinical_years'] = st.number_input("**6.2 임상 경력 년차 (※ 6. 상담 경험 있는 응답자만 기재)**", min_value=0, max_value=50, value=0, step=1)
            
            st.session_state.data['communication_difficulty'] = st.text_area(
                "**7. 실제 환자 또는 모의 환자를 면담하면서 가장 어려웠던 점(의사소통, 증상 파악, 진단, 라포형성 등)을 자유롭게 작성해 주십시오.**",
                placeholder="※ 임상/실습 경험이 있는 경우 : 실제 환자나 모의 환자 면담 시 가장 큰 어려움을 느꼈던 경험을 적어주십시오.\n※ 임상/실습 경험이 없는 경우 : 향후 정신질환 환자를 대면할 때, 가장 어려울 것으로 예상되는 점을 자유롭게 적어주십시오."
            )

            st.divider()
            st.markdown("**[진단 역량 및 활용 정보 평가]**")
            
            st.markdown("**8. 실제 환자를 진단할 때 가장 중요하게 활용하는 정보를 중요한 순서대로 1순위부터 5순위까지 표시해 주십시오.**")
            cues_options = [
                "발화내용", 
                "목소리 톤 및 속도", 
                "표정 및 시선 처리", 
                "신체적 움직임 및 자세", 
                "환자의 외양 및 옷차림"
            ]
            
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1: st.session_state.data['cue_rank_1'] = st.selectbox("1순위", cues_options, index=None)
            with col2: st.session_state.data['cue_rank_2'] = st.selectbox("2순위", cues_options, index=None)
            with col3: st.session_state.data['cue_rank_3'] = st.selectbox("3순위", cues_options, index=None)
            with col4: st.session_state.data['cue_rank_4'] = st.selectbox("4순위", cues_options, index=None)
            with col5: st.session_state.data['cue_rank_5'] = st.selectbox("5순위", cues_options, index=None)

            st.markdown("**9. 귀하는 과거에 임상 실습이나 면담 훈련을 위해 다음과 같은 '환자 시뮬레이션 훈련 또는 실제 임상 참관'을 경험해 본 적이 있습니까? (해당하는 것 모두 선택)**")
            cb_none = st.checkbox("경험 없음")
            cb_shadowing = st.checkbox("지도감독자(Supervisor) 또는 선배의 실제 진료 참관")
            cb_peer = st.checkbox("동료 및 선후배 간의 역할극 (Peer Role-playing)")
            cb_sp = st.checkbox("표준화 환자 (Standarized Patient, 훈련된 모의 환자 연기자) 대면 면담")
            cb_text = st.checkbox("텍스트 기반 환자 시나리오 챗봇")
            cb_vp = st.checkbox("화면 속 아바타/가상환자(Virtual Patient) 시뮬레이션 프로그램")
            cb_video = st.checkbox("사전 녹화된 실제 환자 또는 모의 환자 영상 관찰 훈련")
            cb_other = st.checkbox("기타")
            other_text = ""
            if cb_other:
                other_text = st.text_input("기타 사항을 구체적으로 기재해 주십시오.")
            # ---------------------------------------------------------
            # 제출 및 무결성 검증 (Validation)
            # ---------------------------------------------------------
            if st.form_submit_button("실험 시작하기"):
                # 1. 필수 문항 응답 확인
                required_keys = [
                    'name', 'gender', 'birth_date', 'major', 'certifications', 'clinical_experience', 
                    'consulted_disorders', 'communication_difficulty', 
                    'cue_rank_1', 'cue_rank_2', 'cue_rank_3', 'cue_rank_4', 'cue_rank_5'
                ]
                if not all(st.session_state.data.get(k) for k in required_keys):
                    st.warning("모든 문항을 빠짐없이 입력해 주십시오.")
                    st.stop()
                
                # 2. 15번 문항 논리 통제 (중복 선택 방지)
                selected_cues = [
                    st.session_state.data['cue_rank_1'], 
                    st.session_state.data['cue_rank_2'], 
                    st.session_state.data['cue_rank_3'],
                    st.session_state.data['cue_rank_4'],
                    st.session_state.data['cue_rank_5']
                ]
                if len(set(selected_cues)) != 5:
                    st.error("8번 문항: 1순위부터 5순위까지 서로 다른 항목을 선택해 주십시오. (중복 불가)")
                    st.stop()
                
                # 3. 임상 경험 모순 통제
                exp = st.session_state.data['clinical_experience']
                disorders = st.session_state.data.get('consulted_disorders', '').strip()
                years = st.session_state.data['clinical_years']
                
                if exp == "없음":
                    # 경험이 없는데 지시문대로 '없음'이라고 적지 않았거나, 연차를 1 이상으로 적은 경우 차단
                    if disorders != "없음":
                        st.error("6번 문항에서 면담 경험 '없음'을 선택하셨습니다. 6.1(면담 질환) 칸에 '없음'이라고 기재해 주십시오.")
                        st.stop()
                    if years > 0:
                        st.error("6번 문항에서 면담 경험 '없음'을 선택하셨으므로, 6.2(임상 경력)는 0이어야 합니다.")
                        st.stop()
                else:
                    # 경험이 있는데 질환을 '없음'이라고 적은 경우 차단
                    if disorders == "없음":
                        st.error("6번 문항에서 면담 경험이 있다고 응답하셨습니다. 6.1(면담 질환)에 실제 면담하신 질환명을 기재해 주십시오.")
                        st.stop()
                
                selected_checkboxes = [cb_shadowing, cb_peer, cb_sp, cb_text, cb_vp, cb_video, cb_other]

                if cb_none and any(selected_checkboxes):
                    st.error("9번 문항: '경험 없음'과 다른 훈련 경험 항목을 동시에 선택할 수 없습니다.")
                    st.stop()
                    
                if cb_other and not other_text.strip():
                    st.error("9번 문항: '기타'를 선택하신 경우, 아래 텍스트 칸에 구체적인 사항을 기재해 주십시오.")
                    st.stop()
                
                if not cb_none and not any(selected_checkboxes):
                    st.error("9번 문항: 최소 하나 이상의 항목을 선택해 주십시오. (해당 사항이 없으면 '경험 없음' 선택)")
                    st.stop()
                # 9번 문항 다중 선택 결과 저장

                selected_experiences = []
                if cb_none: selected_experiences.append("경험 없음")
                if cb_shadowing: selected_experiences.append("실제 진료 참관")
                if cb_peer: selected_experiences.append("동료/선후배 역할극")
                if cb_sp: selected_experiences.append("표준화 환자")
                if cb_text: selected_experiences.append("텍스트 챗봇")
                if cb_vp: selected_experiences.append("가상 환자 프로그램")
                if cb_video: selected_experiences.append("영상 관찰 훈련")
                if cb_other and other_text.strip(): 
                    selected_experiences.append(f"기타: {other_text}")

                st.session_state.data['simulation_experience'] = selected_experiences
                save_intermediate_data(current_step="demography")
                # 검증 완료 후 이동
                st.session_state.step = 'task1_instructions'
                st.rerun()
    
    # ---------------------------------------------------------
    # [Step] TASK 1 - Instructions (Task 1 사전 안내)
    # ---------------------------------------------------------
    elif step == 'task1_instructions':
        st.title("파트 1: 우울증 심각도 평가 안내")
        st.markdown("<br>", unsafe_allow_html=True)
        
        html_t1 = """
        <div style="background-color: #ffffff; padding: 35px; border-radius: 12px; border-left: 6px solid #1f77b4; box-shadow: 0px 4px 12px rgba(0,0,0,0.05);">
            <h3 style="color: #1f77b4; margin-top: 0; margin-bottom: 20px;"> Task 1 진행 방식</h3>
            <p style="font-size: 16px; line-height: 1.6; color: #333;">
                지금부터 <b>첫 번째 파트(Task 1)</b>가 시작됩니다.<br>
                Task 1에서 등장하는 모든 가상 환자는 <b>'주요우울장애(Major Depressive Disorder)'</b>를 앓고 있는 것으로 설정되어 있습니다.
            </p>
            <hr style="border: 0; height: 1px; background: #eee; margin: 20px 0;">
            <ul style="font-size: 15px; color: #555; line-height: 1.8; margin-bottom: 20px;">
                <li>영상을 주의 깊게 관찰한 후, 해당 환자가 겪고 있는 우울증의 <b>증상 심각도(Severity)</b>를 평가해 주십시오.</li>
                <li>개별 영상 평가가 끝날 때마다 시스템에 <b>실제 설계된 정답(목표 심각도)</b>이 즉시 공개됩니다.</li>
                <li>정답 확인 후, 가상 환자가 해당 심각도를 얼마나 정확하게 표현했는지 평가해 주시면 됩니다.</li>
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
    # [Step] TASK 1 - Phase 1 (우울증 심각도 평가)
    # ---------------------------------------------------------
    elif step == 'task1_phase1':
        video_id = st.session_state.task1_videos[st.session_state.v_idx]
        required_time = VIDEO_LENGTHS.get(video_id, 60)
        
        st.title("[Task 1] 우울증 심각도 평가")
        st.write(f"### 임상적 증상 평가  {st.session_state.v_idx + 1} / {len(st.session_state.task1_videos)}")
        # st.markdown("**정확한 평가를 위해 영상을 전체화면으로 전환한 후 시청해 주시기 바랍니다.**")
        
        if f"play_started_{video_id}_p1" not in st.session_state:
            st.session_state[f"play_started_{video_id}_p1"] = False
            st.session_state[f"start_time_{video_id}_p1"] = 0
            st.session_state[f"unlocked_{video_id}_p1"] = False
        is_unlocked = st.session_state.get(f"unlocked_{video_id}_p1", False)

        # [핵심 변경] 아직 시청 완료(언락)되지 않은 경우에만 영상과 시청 관련 컨트롤을 보여줌
        if not is_unlocked:
            st.markdown("**정확한 평가를 위해 영상을 전체화면으로 전환한 후 시청해 주시기 바랍니다.**")
            
            if not st.session_state[f"play_started_{video_id}_p1"]:
                if st.button("▶️ 영상 시청 시작", key=f"start_btn_{video_id}_p1"):
                    st.session_state[f"play_started_{video_id}_p1"] = True
                    st.session_state[f"start_time_{video_id}_p1"] = time.time()
                    st.rerun()
                st.stop()
            else:
                video_path = f"videos/{video_id}.mp4"
                with open(video_path, "rb") as v_file: video_bytes = v_file.read()
                encoded_video = base64.b64encode(video_bytes).decode()
                
                v_dom_id = f"vid_{video_id}_{uuid.uuid4().hex[:6]}"

                video_component_html = f'''
                    <div style="position: relative; width: 100%; background: black; line-height: 0;">
                        <video id="{v_dom_id}" width="100%" autoplay playsinline style="pointer-events: none; display: block;">
                            <source src="data:video/mp4;base64,{encoded_video}" type="video/mp4">
                        </video>
                        <button onclick="
                            var elem = document.getElementById('{v_dom_id}');
                            if (elem.requestFullscreen) {{
                                elem.requestFullscreen();
                            }} else if (elem.webkitRequestFullscreen) {{
                                elem.webkitRequestFullscreen();
                            }} else if (elem.msRequestFullscreen) {{
                                elem.msRequestFullscreen();
                            }}
                        " style="
                            position: absolute; 
                            bottom: 20px; 
                            right: 20px; 
                            background-color: rgba(0, 0, 0, 0.7); 
                            color: white; 
                            border: 1px solid rgba(255, 255, 255, 0.4); 
                            padding: 8px 14px; 
                            border-radius: 4px; 
                            cursor: pointer; 
                            font-size: 14px; 
                            font-weight: bold;
                            z-index: 10;
                            font-family: sans-serif;
                        ">
                            📺 전체화면으로 보기
                        </button>
                    </div>
                '''
                components.html(video_component_html, height=1090)

                if f"start_time_{video_id}_p1" not in st.session_state:
                    st.session_state[f"start_time_{video_id}_p1"] = time.time()
                
                elapsed = time.time() - st.session_state[f"start_time_{video_id}_p1"]
                                
                if st.button("평가 문항 열기", key=f"unlock_btn_{video_id}_p1"):
                    if elapsed < required_time:
                        st.error(f"아직 영상 시청이 완료되지 않았습니다.")
                    else:
                        st.session_state[f"unlocked_{video_id}_p1"] = True
                        t_prefix = "Task1" if step == 'task1_phase1' else "Task2"
                        st.session_state.time_logs[f"{t_prefix}_{video_id}_Video_Time"] = round(elapsed, 2)
                        st.session_state[f"survey_start_time_{video_id}_p1"] = time.time()
                        st.rerun()
                st.stop()

        with st.form(f"survey_part1_t2_{video_id}"):
            st.write("**[안내] 이 가상 환자의 질환은 '주요우울장애(Major Depressive Disorder)'입니다. 해당 질환을 바탕으로 환자의 증상 심각도를 평가해 주십시오.**")
            
            st.session_state.data[f"{video_id}_q11_severity"] = st.selectbox(
                                "심각도 선택",
                                ["None (증상 없음)", "Mild", "Moderate", "Severe"],
                                index=None
                            )
            st.write("**2. 아래 항목을 위 환자의 심각도를 판단하는 데 영향을 미친 순서대로 1순위부터 5순위까지 표시해 주십시오. (1 = 가장 큰 영향을 미친 항목, 5 = 가장 작은 영향을 미친 항목)**")

            cues_options = [
                "발화 내용", 
                "목소리 톤 및 속도", 
                "표정 및 시선 처리", 
                "신체적 움직임 및 자세", 
                "환자의 외양 및 옷차림"
            ]

            # 5열 배치 구성 (해상도에 따라 텍스트 잘림 현상 발생 가능성 존재)
            col1, col2, col3, col4, col5 = st.columns(5)

            # 동일 영상에 대한 다중 렌더링 충돌을 막기 위해 key 값에 video_id 할당
            with col1: 
                st.session_state.data[f"{video_id}_cue_rank_1"] = st.selectbox("1순위", cues_options, index=None, key=f"{video_id}_r1")
            with col2: 
                st.session_state.data[f"{video_id}_cue_rank_2"] = st.selectbox("2순위", cues_options, index=None, key=f"{video_id}_r2")
            with col3: 
                st.session_state.data[f"{video_id}_cue_rank_3"] = st.selectbox("3순위", cues_options, index=None, key=f"{video_id}_r3")
            with col4: 
                st.session_state.data[f"{video_id}_cue_rank_4"] = st.selectbox("4순위", cues_options, index=None, key=f"{video_id}_r4")
            with col5: 
                st.session_state.data[f"{video_id}_cue_rank_5"] = st.selectbox("5순위", cues_options, index=None, key=f"{video_id}_r5")
            
            st.write("**3. 2번 문항에서 선택한 단서들을 바탕으로, 위 환자의 심각도를 해당 수준으로 진단한 구체적인 이유를 적어주십시오**")

            st.session_state.data[f"{video_id}_q13_reason"] = st.text_area("판단 근거")

            if st.form_submit_button("평가 제출"):
                req = [
                    f"{video_id}_q11_severity", f"{video_id}_q13_reason",
                    f"{video_id}_cue_rank_1", f"{video_id}_cue_rank_2", 
                    f"{video_id}_cue_rank_3", f"{video_id}_cue_rank_4", f"{video_id}_cue_rank_5"
                ]
                if not all(st.session_state.data.get(k) for k in req): 
                    st.error("모든 평가 문항에 빠짐없이 응답해 주십시오.")
                    st.stop()
                
                # 검증 2: 순위 중복 선택 논리 통제
                selected_cues = [
                    st.session_state.data[f"{video_id}_cue_rank_1"], 
                    st.session_state.data[f"{video_id}_cue_rank_2"], 
                    st.session_state.data[f"{video_id}_cue_rank_3"],
                    st.session_state.data[f"{video_id}_cue_rank_4"],
                    st.session_state.data[f"{video_id}_cue_rank_5"]
                ]
                if len(set(selected_cues)) != 5:
                    st.error("2번 문항: 1순위부터 5순위까지 서로 다른 항목을 선택해 주십시오. (중복 불가)")
                    st.stop()
                tagged_cues = [
                    f"1순위: {selected_cues[0]}", 
                    f"2순위: {selected_cues[1]}", 
                    f"3순위: {selected_cues[2]}",
                    f"4순위: {selected_cues[3]}",
                    f"5순위: {selected_cues[4]}"
                ]
                st.session_state.data[f"{video_id}_q12_cues"] = tagged_cues
                t_prefix = "Task1" if step == 'task1_phase1' else "Task2"
                survey_time_p1 = time.time() - st.session_state[f"survey_start_time_{video_id}_p1"]
                st.session_state.time_logs[f"{t_prefix}_{video_id}_Phase1_Survey_Time"] = round(survey_time_p1, 2)

                # [핵심] 페이지 전환 전 중간 저장(Checkpoint) 수행
                save_intermediate_data(current_step=f"task1_phase1_{video_id}")
                
                st.session_state.step = 'task1_intermission'
                st.rerun()

    # ---------------------------------------------------------
    # [Step] TASK 1 - Intermission (정답 공개)
    # ---------------------------------------------------------
    elif step == 'task1_intermission':
        video_id = st.session_state.task1_videos[st.session_state.v_idx]
        
        # 팩트 체크 1: 질환명과 심각도(Severity)를 모두 가져옵니다.
        gt_diag = GROUND_TRUTH.get(video_id, {}).get("diagnosis", "주요우울장애")
        gt_sev = GROUND_TRUTH.get(video_id, {}).get("severity", "미상")

        st.markdown("<br><br>", unsafe_allow_html=True)

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
            if st.button("시스템 평가 시작하기", use_container_width=True, key=f"sys_eval_t1_{video_id}"):
                st.session_state[f"survey_start_time_{video_id}_p2"] = time.time()
                st.session_state.step = 'task1_phase2' 
                st.rerun()
    # ---------------------------------------------------------
    # [Step] TASK 1 - Phase 2 (시스템 평가)
    # ---------------------------------------------------------
    elif step == 'task1_phase2':
        video_id = st.session_state.task1_videos[st.session_state.v_idx]
        gt_diag = GROUND_TRUTH.get(video_id, {}).get("diagnosis", "미상")
        gt_sev = GROUND_TRUTH.get(video_id, {}).get("severity", "미상")

        st.title("[Task 1] 시스템 품질 및 경험 평가")
        #st.write(f"### 대상 환자 {st.session_state.v_idx + 1} / {len(st.session_state.task2_videos)}")
        st.info(f"이 가상 환자의 정답 기준: **[질환: {gt_diag} / 심각도: {gt_sev}]**")

        render_system_evaluation_form(video_id, task_num=1, v_idx=st.session_state.v_idx)

    # ---------------------------------------------------------
    # [Step] TASK 2 - Instructions (Task 2 사전 안내)
    # ---------------------------------------------------------
    elif step == 'task2_instructions':
        st.title("파트 2: 질환 종류 진단 평가 안내")
        st.markdown("<br>", unsafe_allow_html=True)
        
        html_t2 = """
        <div style="background-color: #ffffff; padding: 35px; border-radius: 12px; border-left: 6px solid #ff7f0e; box-shadow: 0px 4px 12px rgba(0,0,0,0.05);">
            <h3 style="color: #ff7f0e; margin-top: 0; margin-bottom: 20px;"> Task 2 진행 방식</h3>
            <p style="font-size: 16px; line-height: 1.6; color: #333;">
                수고하셨습니다. 이제 <b>두 번째 파트(Task 2)</b>가 시작됩니다.<br>
                본 파트에서는 가상 환자 영상을 시청하며 임상적 진단을 수행하게 됩니다.
            </p>
            <hr style="border: 0; height: 1px; background: #eee; margin: 20px 0;">
            <ul style="font-size: 15px; color: #555; line-height: 1.8; margin-bottom: 20px;">
                <li>영상을 관찰한 후, 환자의 <b>가장 가능성 높은 질환명(진단명)</b>을 평가해 주십시오.</li>
                <li>개별 영상 평가가 끝날 때마다 시스템에 <b>실제 설계된 정답</b>이 즉시 공개됩니다.</li>
                <li>정답을 기준으로 가상 환자가 질환을 얼마나 사실적으로 묘사했는지 평가해 주시면 됩니다.</li>
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
    # [Step] TASK 2 - Phase 1 (질환 종류 평가)
    # ---------------------------------------------------------
    elif step == 'task2_phase1':
        video_id = st.session_state.task2_videos[st.session_state.v_idx]
        required_time = VIDEO_LENGTHS.get(video_id, 60)
        
        st.title("[Task 2] 질환 종류 진단 평가")
        st.write(f"### 임상적 진단 평가  {st.session_state.v_idx + 1} / {len(st.session_state.task2_videos)}")
        # st.markdown("**정확한 평가를 위해 영상을 전체화면으로 전환한 후 시청해 주시기 바랍니다.**")

        if f"play_started_{video_id}_p1" not in st.session_state:
            st.session_state[f"play_started_{video_id}_p1"] = False
            st.session_state[f"start_time_{video_id}_p1"] = 0
            st.session_state[f"unlocked_{video_id}_p1"] = False
        is_unlocked = st.session_state.get(f"unlocked_{video_id}_p1", False)

        # [핵심 변경] 아직 시청 완료(언락)되지 않은 경우에만 영상과 시청 관련 컨트롤을 보여줌
        if not is_unlocked:
            st.markdown("**정확한 평가를 위해 영상을 전체화면으로 전환한 후 시청해 주시기 바랍니다.**")
            
            if not st.session_state[f"play_started_{video_id}_p1"]:
                if st.button("▶️ 영상 시청 시작", key=f"start_btn_{video_id}_p1"):
                    st.session_state[f"play_started_{video_id}_p1"] = True
                    st.session_state[f"start_time_{video_id}_p1"] = time.time()
                    st.rerun()
                st.stop()
            else:
                video_path = f"videos/{video_id}.mp4"
                with open(video_path, "rb") as v_file: video_bytes = v_file.read()
                encoded_video = base64.b64encode(video_bytes).decode()
                
                v_dom_id = f"vid_{video_id}_{uuid.uuid4().hex[:6]}"

                video_component_html = f'''
                    <div style="position: relative; width: 100%; background: black; line-height: 0;">
                        <video id="{v_dom_id}" width="100%" autoplay playsinline style="pointer-events: none; display: block;">
                            <source src="data:video/mp4;base64,{encoded_video}" type="video/mp4">
                        </video>
                        <button onclick="
                            var elem = document.getElementById('{v_dom_id}');
                            if (elem.requestFullscreen) {{
                                elem.requestFullscreen();
                            }} else if (elem.webkitRequestFullscreen) {{
                                elem.webkitRequestFullscreen();
                            }} else if (elem.msRequestFullscreen) {{
                                elem.msRequestFullscreen();
                            }}
                        " style="
                            position: absolute; 
                            bottom: 20px; 
                            right: 20px; 
                            background-color: rgba(0, 0, 0, 0.7); 
                            color: white; 
                            border: 1px solid rgba(255, 255, 255, 0.4); 
                            padding: 8px 14px; 
                            border-radius: 4px; 
                            cursor: pointer; 
                            font-size: 14px; 
                            font-weight: bold;
                            z-index: 10;
                            font-family: sans-serif;
                        ">
                            📺 전체화면으로 보기
                        </button>
                    </div>
                '''
                components.html(video_component_html, height=1090)

                if f"start_time_{video_id}_p1" not in st.session_state:
                    st.session_state[f"start_time_{video_id}_p1"] = time.time()
                
                elapsed = time.time() - st.session_state[f"start_time_{video_id}_p1"]
                                
                if st.button("평가 문항 열기", key=f"unlock_btn_{video_id}_p1"):
                    if elapsed < required_time:
                        st.error(f"아직 영상 시청이 완료되지 않았습니다.")
                    else:
                        st.session_state[f"unlocked_{video_id}_p1"] = True
                        t_prefix = "Task1" if step == 'task1_phase1' else "Task2"
                        st.session_state.time_logs[f"{t_prefix}_{video_id}_Video_Time"] = round(elapsed, 2)
                        st.session_state[f"survey_start_time_{video_id}_p1"] = time.time()
                        st.rerun()
                st.stop()

        with st.form(f"survey_part1_t1_{video_id}"):
            st.markdown("**1. 이 환자의 가장 가능성 높은 질환(진단명)은 무엇이라고 생각하십니까?**")
            st.session_state.data[f"{video_id}_q10_category"] = st.selectbox(
                                "질환 선택",
                                ["None (해당 없음)", "Obsessive-Compulsive Disorder", "Major Depressive Disorder", "Generalized Anxiety Disorder"],
                                index=None
                            )

            st.write("**2. 아래 항목을 위 환자의 질환을 판단하는 데 영향을 미친 순서대로 1순위부터 5순위까지 표시해 주십시오. (1 = 가장 큰 영향을 미친 항목, 5 = 가장 작은 영향을 미친 항목)**")

            cues_options = [
                "발화 내용", 
                "목소리 톤 및 속도", 
                "표정 및 시선 처리", 
                "신체적 움직임 및 자세", 
                "환자의 외양 및 옷차림"
            ]

            # 5열 배치 구성 (해상도에 따라 텍스트 잘림 현상 발생 가능성 존재)
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1: 
                st.session_state.data[f"{video_id}_cue_rank_1"] = st.selectbox("1순위", cues_options, index=None, key=f"{video_id}_r1")
            with col2: 
                st.session_state.data[f"{video_id}_cue_rank_2"] = st.selectbox("2순위", cues_options, index=None, key=f"{video_id}_r2")
            with col3: 
                st.session_state.data[f"{video_id}_cue_rank_3"] = st.selectbox("3순위", cues_options, index=None, key=f"{video_id}_r3")
            with col4: 
                st.session_state.data[f"{video_id}_cue_rank_4"] = st.selectbox("4순위", cues_options, index=None, key=f"{video_id}_r4")
            with col5: 
                st.session_state.data[f"{video_id}_cue_rank_5"] = st.selectbox("5순위", cues_options, index=None, key=f"{video_id}_r5")

            st.write("**3. 2번 문항에서 선택한 단서들을 바탕으로, 위 환자를 해당 질환으로 진단한 구체적인 이유를 적어주십시오**")
            st.session_state.data[f"{video_id}_q13_reason"] = st.text_area("판단 근거")
            
            if st.form_submit_button("평가 제출"):
                req = [f"{video_id}_q10_category", f"{video_id}_q13_reason",
                    f"{video_id}_cue_rank_1", f"{video_id}_cue_rank_2", 
                    f"{video_id}_cue_rank_3", f"{video_id}_cue_rank_4", f"{video_id}_cue_rank_5"]
                
                if not all(st.session_state.data.get(k) for k in req): st.error("모든 평가 문항에 응답해 주십시오."); st.stop()
                
                selected_cues = [
                    st.session_state.data[f"{video_id}_cue_rank_1"], 
                    st.session_state.data[f"{video_id}_cue_rank_2"], 
                    st.session_state.data[f"{video_id}_cue_rank_3"],
                    st.session_state.data[f"{video_id}_cue_rank_4"],
                    st.session_state.data[f"{video_id}_cue_rank_5"]
                ]
                if len(set(selected_cues)) != 5:
                    st.error("2번 문항: 1순위부터 5순위까지 서로 다른 항목을 선택해 주십시오. (중복 불가)")
                    st.stop()
                tagged_cues = [
                    f"1순위: {selected_cues[0]}", 
                    f"2순위: {selected_cues[1]}", 
                    f"3순위: {selected_cues[2]}",
                    f"4순위: {selected_cues[3]}",
                    f"5순위: {selected_cues[4]}"
                ]
                st.session_state.data[f"{video_id}_q12_cues"] = tagged_cues
                t_prefix = "Task1" if step == 'task1_phase1' else "Task2"
                survey_time_p1 = time.time() - st.session_state[f"survey_start_time_{video_id}_p1"]
                st.session_state.time_logs[f"{t_prefix}_{video_id}_Phase1_Survey_Time"] = round(survey_time_p1, 2)

                save_intermediate_data(current_step=f"task2_phase1_{video_id}")
                
                st.session_state.step = 'task2_intermission'
                st.rerun()

    # ---------------------------------------------------------
    # [Step] TASK 2 - Intermission (정답 공개)
    # ---------------------------------------------------------
    elif step == 'task2_intermission':
        video_id = st.session_state.task2_videos[st.session_state.v_idx]
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
            if st.button("시스템 평가 시작하기", use_container_width=True, key=f"sys_eval_t2_{video_id}"):
                st.session_state[f"survey_start_time_{video_id}_p2"] = time.time()
                st.session_state.step = 'task2_phase2'
                st.rerun()


    # ---------------------------------------------------------
    # [Step] TASK 2 - Phase 2 (시스템 평가)
    # ---------------------------------------------------------
    elif step == 'task2_phase2':
        video_id = st.session_state.task2_videos[st.session_state.v_idx]
        gt_diag = GROUND_TRUTH.get(video_id, {}).get("diagnosis", "미상")

        st.title("[Task 2] 시스템 품질 및 경험 평가")
        st.info(f"이 가상 환자의 정답 기준: **[질환: {gt_diag}]**")
        
        render_system_evaluation_form(video_id, task_num=2, v_idx=st.session_state.v_idx)

    # ---------------------------------------------------------
    # [Step] Final (종합 평가)
    # ---------------------------------------------------------
    elif step == 'final':
        st.title("가상환자 평가 실험 완료")
        st.subheader("임상 훈련 도구로서의 활용성 및 종합 평가")
        st.info("모든 영상 평가가 완료되었습니다. 마지막으로 본 가상 환자 시스템 전체에 대한 종합적인 의견을 여쭙습니다.")
        
        with st.form("final_comprehensive_survey"):
            st.session_state.data["q26_overall_exp"] = st.radio("**1. 가상 환자를 사용한 귀하의 전반적인 경험을 1에서 10까지의 척도로 평가해 주십시오. (1점은 '매우 나쁨', 10점은 '매우 좋음)**", [str(i) for i in range(1, 11)], index=None, horizontal=True)
            st.session_state.data["q27_reuse_intent"] = st.radio("**2. 향후 훈련 과정 중에 가상 환자를 다시 사용할 의향이 얼마나 있습니까? (1점은 '전혀 관심이 없음', 10점은 '매우 관심이 있음')**", [str(i) for i in range(1, 11)], index=None, horizontal=True)
            
            st.markdown("""
            **3. 앞선 실험에서 평가했던 아래의 질환 목록을 참고하여, 진단이 가장 어렵거나 감별하기 헷갈렸던 질환은 무엇이며 그 이유는 무엇인지 자세히 서술해 주십시오.**

            > **[참고: 실험 진행 질환 목록]**
            > * **Task 1** : Major Depressive Disorder(Severe), Major Depressive Disorder(Moderate), Major Depressive Disorder(Mild), None
            > * **Task 2** : Obsessive-Compulsive Disorder, Generalized Anxiety Disorder, Major Depressive Disorder, None
            """)

            # 질문 텍스트는 위에서 마크다운으로 처리했으므로, text_area의 label은 숨김(collapsed) 처리
            st.session_state.data["q28_diff_diagnosis"] = st.text_area("어떤 질환의 감별이 가장 어려웠습니까?")

            st.session_state.data["q29_pros"] = st.text_area("**4. 임상 교육 도구로서 본 가상 환자 시스템의 가장 큰 장점은 무엇이라고 생각하십니까?**")
            st.session_state.data["q30_cons"] = st.text_area("**5. 본 가상 환자 시스템에서 이질감을 느꼈던 부분이나 개선되어야 할 점이 있다면 제안해 주십시오.**")

            if st.form_submit_button("최종 데이터 제출 및 실험 종료"):
                if not all([st.session_state.data.get("q26_overall_exp"), st.session_state.data.get("q27_reuse_intent"), st.session_state.data.get("q28_diff_diagnosis"), st.session_state.data.get("q29_pros"), st.session_state.data.get("q30_cons")]):
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
            # [타임로그 추가] Task 1 및 Task 2 총합 소요 시간 계산
            task1_sum = 0.0
            task2_sum = 0.0
            for k, v in st.session_state.time_logs.items():
                if k.startswith("Task1_") and k.endswith("_Time") and "Total" not in k:
                    task1_sum += v
                elif k.startswith("Task2_") and k.endswith("_Time") and "Total" not in k:
                    task2_sum += v
            
            st.session_state.time_logs["Task1_Total_Time"] = round(task1_sum, 2)
            st.session_state.time_logs["Task2_Total_Time"] = round(task2_sum, 2)

            save_success = save_intermediate_data(current_step="final_save")
            if not save_success:
                st.error(
                    "최종 데이터 저장에 실패했습니다. "
                    "페이지를 닫지 말고 다시 시도해 주십시오."
                )
                st.stop()
            else:
                st.session_state.step = 'done'
                st.rerun()

    elif step == 'done':
        st.balloons()
        st.success("설문이 모두 완료되었습니다. 연구에 참여해 주셔서 진심으로 감사드립니다.")
        st.write("안전하게 창을 닫아주셔도 좋습니다.")

def render_system_evaluation_form(video_id, task_num, v_idx):
    with st.form(f"survey_part2_{video_id}_{v_idx}"):
        st.write("*아래의 모든 평가 기준은 **실제 정답값**을 바탕으로 합니다.*")
        st.subheader("가상 환자 추가 피드백")
        st.markdown("**1. 영상 속 가상 환자의 표정, 행동, 발화 내용, 음성 등에 대해 긍정적인 부분과 개선이 필요한 부분을 작성해 주십시오.**")
        st.write("*(설계된 정답 질환의 특성을 잘 살린 부분이나, 반대로 더 현실감을 높이기 위해 수정해야 할 점을 구체적으로 적어주십시오.)*")
        
        st.session_state.data[f"{video_id}_feedback_pros"] = st.text_area("좋았던 점 (Strengths)", placeholder="가상 환자의 긍정적인 부분이나 현실적이었던 점을 적어주세요.")
        st.session_state.data[f"{video_id}_feedback_cons"] = st.text_area("부족했던 점 (Weaknesses)", placeholder="개선이 필요한 부분이나 부자연스러웠던 점을 적어주세요.")

        st.subheader("가상 환자 종합 평가")
        st.session_state.data[f"{video_id}_q14_humanlikeness"] = st.radio(
            "**2. 가상 환자는 인간 상호작용에서 흔히 볼 수 있는 특성을 보였습니까, 아니면 자동적인 존재처럼 보였습니까?**",
            [
                "1점 - 인간과 닮지 않음 (감정적 미묘함, 상황 인식 및 자발성이 부족하여 일관되게 인위적인 모습을 보입니다.)",
                "2점 - 약간 인간과 유사함 (종종 기계적인 느낌을 주며, 경직된 패턴, 반복적인 표현, 부자연스러운 반응을 보입니다.)",
                "3점 - 다소 인간과 유사함 (인간과 유사한 경향을 보이지만, 때때로 정해진 각본대로 행동하거나 자연스러운 행동 변화가 부족해 보입니다.)",
                "4점 - 대체로 인간과 유사함 (감정 표현이나 반응 패턴에 약간의 불일치가 있을 뿐, 전반적으로 인간과 유사한 방식으로 행동합니다.)",
                "5점 - 매우 인간과 유사함 (실제 인간에게서 볼 수 있는 풍부하고 미묘한 뉘앙스와 예측 불가능한 행동을 보입니다. 반응에는 감정, 미묘한 어조 변화, 적절한 망설임이 포함됩니다.)"
            ], index=None
        )
        
        st.session_state.data[f"{video_id}_q15_naturalness"] = st.radio(
            "**3. 가상 환자의 의사소통 행동이 실제 사람들의 행동과 일치했습니까?**",
            [
                "1점 - 매우 부자연스러움 (기계적이고 부자연스럽거나 상황에 맞지 않는 방식으로 의사소통하여 상호작용이 인위적으로 느껴집니다.)",
                "2점 - 다소 부자연스러움 (대화가 부자연스럽고, 로봇 같거나, 지나치게 대본처럼 느껴져 현실감이 떨어집니다.)",
                "3점 - 보통 (환자의 말 흐름은 적절하지만, 때때로 경직되거나 지나치게 격식적인 언어를 사용하여 자연스러움이 떨어집니다.)",
                "4점 - 대체로 자연스러움 (대체로 현실적인 방식으로 의사소통하며, 부자연스러운 표현이나 상호작용은 가끔씩만 나타납니다.)",
                "5점 - 매우 자연스러움 (의사소통 방식, 어조 및 표현이 실제 사람 상호작용과 완벽하게 일치합니다. 다양한 대화 신호에 자연스럽게 적응합니다.)"
            ], index=None
        )
        
        st.session_state.data[f"{video_id}_q16_fluency"] = st.radio(
            "**4. 가상 환자가 일관성 있고 매끄러운 방식으로 의사소통을 했습니까?**",
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
        if task_num == 1:
            # Task 1: 우울증 심각도 평가 목적에 맞춘 워딩
            st.session_state.data[f"{video_id}_q17_realism"] = st.radio(
                "**5. 가상 환자가 할당된 심각도와 일치하는 방식으로 증상을 보였습니까?**",
                [
                    "1점 - 전혀 현실적이지 않음 (질환 심각도와 관련 없는 증상을 나타내거나 현실적인 증상이 없습니다.)",
                    "2점 - 다소 비현실적임 (증상이 종종 불완전하거나, 잘못 표현되거나, 피상적으로 표현됩니다.)",
                    "3점 - 보통 (일부 증상은 임상적 기대치와 일치하지만, 다른 증상은 과장되거나, 나타나지 않거나 일관성이 없습니다.)",
                    "4점 - 대체로 현실적임 (대부분의 증상이 정확하게 표현되었으며, 사소한 부정확함이나 세부 정보 누락만 있을 뿐입니다.)",
                    "5점 - 매우 현실적임 (광범위한 질환 심각도 관련 증상을 정확하게 나타냅니다.)"
                ], index=None
            )
            
            st.session_state.data[f"{video_id}_q18_consistency"] = st.radio(
                "**6. 가상 환자가 할당된 심각도에 맞춰 감정적, 인지적 패턴을 일관되게 유지했습니까?**",
                [
                    "1점 - 전혀 일관되지 않음 (환자의 감정 표현이 무작위적이거나 모순되어 신뢰성이 떨어집니다.)",
                    "2점 - 다소 일관되지 않음 (감정적 반응의 잦은 불일치는 심각도 수준의 변동과 같은 현실성을 감소시킵니다.)",
                    "3점 - 보통 (때때로 심각도와 일치하지만 가끔 강도나 적절성이 달라지기도 합니다.)",
                    "4점 - 대체로 일관됨 (일반적으로 적절한 감정적 반응을 유지하지만 사소한 편차나 불일치가 있습니다.)",
                    "5점 - 매우 일관됨 (상호작용 내내 일치하는 안정적인 감정적, 인지적 패턴을 유지합니다.)"
                ], index=None
            )
            
            st.session_state.data[f"{video_id}_q19_cognitive"] = st.radio(
                "**7. 가상 환자의 발화가 할당된 심각도와 관련된 인지 처리 패턴을 잘 반영했습니까?**",
                [
                    "1점 - 전혀 반영하지 않음 (질환 심각도와 관련된 의미 있는 인지 처리 패턴을 전혀 나타내지 않아 신뢰성이 떨어집니다.)",
                    "2점 - 다소 반영하지 않음 (인지 패턴이 약하게 표현되거나 때로는 알려진 질환 특성과 모순됩니다.)",
                    "3점 - 보통 (일부 질환 심각도와 관련된 인지 특성이 존재하지만 일관성 있게 표현되지 않거나 항상 일치하지는 않습니다.)",
                    "4점 - 대체로 반영함 (일반적으로 적절한 인지 처리 패턴을 보이지만 약간의 불일치가 있습니다.)",
                    "5점 - 매우 정확히 반영함 (임상적으로 타당하고 일관된 방식으로 질환 심각도와 관련된 인지 패턴을 보여줍니다.)"
                ], index=None
            )

        else:
            # Task 2: 질환(진단명) 감별 목적에 맞춘 워딩
            st.session_state.data[f"{video_id}_q17_realism"] = st.radio(
                "**5. 가상 환자가 할당된 질환과 일치하는 방식으로 증상을 보였습니까?**",
                [
                    "1점 - 전혀 현실적이지 않음 (질환과 관련 없는 증상을 나타내거나 현실적인 증상이 없습니다.)",
                    "2점 - 다소 비현실적임 (증상이 종종 불완전하거나, 잘못 표현되거나, 피상적으로 표현됩니다.)",
                    "3점 - 보통 (일부 증상은 임상적 기대치와 일치하지만, 다른 증상은 과장되거나, 나타나지 않거나 일관성이 없습니다.)",
                    "4점 - 대체로 현실적임 (대부분의 증상이 정확하게 표현되었으며, 사소한 부정확함이나 세부 정보 누락만 있을 뿐입니다.)",
                    "5점 - 매우 현실적임 (광범위한 질환 관련 증상을 정확하게 나타냅니다.)"
                ], index=None
            )
            
            st.session_state.data[f"{video_id}_q18_consistency"] = st.radio(
                "**6. 가상 환자가 할당된 질환에 맞춰 감정적, 인지적 패턴을 일관되게 유지했습니까?**",
                [
                    "1점 - 전혀 일관되지 않음 (환자의 감정 표현이 무작위적이거나 모순되어 신뢰성이 떨어집니다.)",
                    "2점 - 다소 일관되지 않음 (감정적 반응의 잦은 불일치는 질환 수준의 변동과 같은 현실성을 감소시킵니다.)",
                    "3점 - 보통 (때때로 질환와 일치하지만 가끔 강도나 적절성이 달라지기도 합니다.)",
                    "4점 - 대체로 일관됨 (일반적으로 적절한 감정적 반응을 유지하지만 사소한 편차나 불일치가 있습니다.)",
                    "5점 - 매우 일관됨 (상호작용 내내 일치하는 안정적인 감정적, 인지적 패턴을 유지합니다.)"
                ], index=None
            )
            
            st.session_state.data[f"{video_id}_q19_cognitive"] = st.radio(
                "**7. 가상 환자의 발화가 할당된 질환과 관련된 인지 처리 패턴을 잘 반영했습니까?**",
                [
                    "1점 - 전혀 반영하지 않음 (질환과 관련된 의미 있는 인지 처리 패턴을 전혀 나타내지 않아 신뢰성이 떨어집니다.)",
                    "2점 - 다소 반영하지 않음 (인지 패턴이 약하게 표현되거나 때로는 알려진 질환 특성과 모순됩니다.)",
                    "3점 - 보통 (일부 질환과 관련된 인지 특성이 존재하지만 일관성 있게 표현되지 않거나 항상 일치하지는 않습니다.)",
                    "4점 - 대체로 반영함 (일반적으로 적절한 인지 처리 패턴을 보이지만 약간의 불일치가 있습니다.)",
                    "5점 - 매우 정확히 반영함 (임상적으로 타당하고 일관된 방식으로 질환과 관련된 인지 패턴을 보여줍니다.)"
                ], index=None
            )

        st.divider()
        st.subheader("가상 환자 경험 평가")

        likert_scales = ["1점 (전혀 동의하지 않음)", "2점 (동의하지 않음)", "3점 (보통/중립)", "4점 (동의함)", "5점 (매우 동의함)"]
        
        st.markdown("**[전문적인 임상 추론]**")
        st.session_state.data[f"{video_id}_q20_reasoning1"] = st.radio("**8. 나는 이 상담 영상을 시청하는 동안, 환자의 상태를 파악하기 위해 필요한 정보를 주의 깊게 확인하였다.**", likert_scales, index=None, horizontal=True)
        st.session_state.data[f"{video_id}_q21_reasoning2"] = st.radio("**9. 나는 이 상담 영상을 시청하는 동안, 새로운 정보를 확인할 때마다 환자의 상태에 대한 나의 판단을 수정하였다.**", likert_scales, index=None, horizontal=True)
        st.session_state.data[f"{video_id}_q22_reasoning4"] = st.radio("**10. 나는 이 상담 영상을 시청하는 동안, 환자에게서 얻은 임상적 단서가 어떤 질환을 뒷받침하고 어떤 질환의 가능성을 낮추는지 지속적으로 생각하였다.**", likert_scales, index=None, horizontal=True)

        st.markdown("**[상담의 학습 효과]**")
        st.session_state.data[f"{video_id}_q23_learning1"] = st.radio("**11. 이 상담 영상을 통해 훈련할 경우, 유사한 증상을 보이는 실제 환자의 질환을 진단하고 다른 질환과 감별하는 데 도움이 될 것이다.**", likert_scales, index=None, horizontal=True)
        st.session_state.data[f"{video_id}_q24_learning2"] = st.radio("**12. 이 상담 영상을 통해 훈련할 경우, 유사한 증상을 보이는 실제 환자를 면담하고 평가하는데 도움이 될 것이다.**", likert_scales, index=None, horizontal=True)
        st.markdown("**[전반적인 평가]**")
        st.session_state.data[f"{video_id}_q25_overall_case"] = st.radio("**13. 전반적으로, 이 상담 영상을 활용한 학습은 유익한 학습 경험이었다.**", likert_scales, index=None, horizontal=True)

        
        if st.form_submit_button("평가 제출"):
            # 1. 객관식 문항 검증
            req_part2 = [f"{video_id}_q{i}_{name}" for i, name in zip(range(14, 26), ['humanlikeness', 'naturalness', 'fluency', 'realism', 'consistency', 'cognitive', 'reasoning1', 'reasoning2', 'reasoning4', 'learning1', 'learning2', 'overall_case'])]
            if not all(st.session_state.data.get(k) for k in req_part2): st.error("모든 객관식 평가 항목에 응답해 주십시오."); st.stop()
            # 2. 주관식 문항 검증
            pros = st.session_state.data.get(f"{video_id}_feedback_pros", "").strip()
            cons = st.session_state.data.get(f"{video_id}_feedback_cons", "").strip()
            if not pros or not cons:
                st.error("좋았던 점과 부족했던 점을 모두 작성해 주십시오.")
                st.stop()
           
            # [타임로그 추가] Phase 2 시스템 평가 소요 시간 기록
            t_prefix = "Task1" if task_num == 1 else "Task2"
            survey_time_p2 = time.time() - st.session_state[f"survey_start_time_{video_id}_p2"]
            st.session_state.time_logs[f"{t_prefix}_{video_id}_Phase2_Time"] = round(survey_time_p2, 2)

            save_intermediate_data(current_step=f"task{task_num}_phase2_{video_id}")
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