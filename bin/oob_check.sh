#!/usr/bin/env bash
# oob_check.sh — 确定性越界检测器（不调 API，<10ms）
# 用法：./bin/oob_check.sh "问题文本"
#
# 输出（action-based 模型，v2）：
#   in_scope                  — 进入正常管道
#   out_of_scope:surgery      — A类：外科手术/介入决策
#   out_of_scope:chemo        — B类：肿瘤化疗具体剂量方案
#   out_of_scope:diagnosis    — C类：要求确诊 / 看化验单判断病情
#   out_of_scope:dosing_change — D类：要求自行调整用药剂量
#   out_of_scope:unrelated    — E类：完全无关任务
#
# v2 变更：移除 C类"未覆盖专科"阻断（神经/肿瘤/精神等现已 in-scope）；
#          新增 C类诊断红线、D类调药红线。

set -euo pipefail

MODE="patient"
QUESTION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    *)      QUESTION="$1"; shift ;;
  esac
done

if [[ -z "$QUESTION" ]]; then
  QUESTION="$(cat)"
fi

if [[ -z "$QUESTION" ]]; then
  echo "in_scope"
  exit 0
fi

# ─── A 类：外科手术 / 介入决策（应拒答）────────────────────────
# 本系统提供内科管理科普；手术指征/介入时机需专科医生综合评估，拒答
KEYWORDS_SURGERY="搭桥手术|冠状动脉搭桥|CABG|心脏手术|换瓣|瓣膜手术|心脏移植|\
心脏搭桥|做搭桥|要搭桥|\
肾移植|肝移植|器官移植|骨髓移植|造血干细胞移植|\
支架手术|放支架|放不放支架|要不要手术|手术指征|要做手术|做手术吗|\
介入治疗|冠脉介入|射频消融|导管消融|心脏消融|\
透析|血液透析|腹膜透析|要不要透析|透析时机|\
手术风险|术前评估|术后护理|内镜手术|胃镜手术|肠镜手术|\
起搏器植入|安装起搏器|做支架|要不要做手术"

if echo "$QUESTION" | grep -qE "$KEYWORDS_SURGERY"; then
  echo "out_of_scope:surgery"
  exit 0
fi

# ─── B 类：肿瘤化疗具体方案/剂量（应拒答）───────────────────
# 肿瘤疾病科普（是什么/副作用/何时就医）属 in-scope；
# 具体化疗药+剂量+方案由肿瘤科医生决定，拒答
KEYWORDS_CHEMO="化疗方案|化疗剂量|化疗用什么药|化疗药物选择|\
骨髓抑制|升白细胞针|升血小板|G-CSF|\
紫杉醇|顺铂|卡铂|奥沙利铂|伊立替康|吉西他滨|培美曲塞|\
靶向治疗用什么药|靶向治疗用哪个药|免疫治疗剂量|PD-1.*剂量|PD-L1.*剂量|CTLA-4.*剂量|\
CAR-T|细胞免疫治疗方案|肿瘤免疫治疗方案|\
放疗剂量|放化疗方案|化放疗.*方案|同步放化疗.*方案|\
CHOP|R-CHOP|利妥昔单抗.*剂量|淋巴瘤.*方案|方案.*淋巴瘤|ABVD|BEP|FOLFOX|FOLFIRI|EC方案|AC方案"

if echo "$QUESTION" | grep -qE "$KEYWORDS_CHEMO"; then
  echo "out_of_scope:chemo"
  exit 0
fi

# ─── C 类：诊断红线（应拒答）──────────────────────────────────
# 本系统不能通过文字帮用户确诊疾病或解读化验单
KEYWORDS_DIAGNOSIS="帮我.*诊断|帮我看.*化验单|帮我看.*报告|帮我看.*片子|\
是不是得了|我是不是患了|我得了什么病|是不是.*病|是不是.*癌|\
确诊|确认.*是什么病|判断.*是什么病|看.*化验.*结果|看.*检查.*结果|\
看化验单|分析化验单|解读.*报告|解读.*化验|这个化验单说明什么|\
我是不是癌症|判断我的病"

if echo "$QUESTION" | grep -qE "$KEYWORDS_DIAGNOSIS"; then
  echo "out_of_scope:diagnosis"
  exit 0
fi

# ─── D 类：调药红线（应拒答）──────────────────────────────────
# 用药剂量调整必须由医生决定，本系统不给出具体加减量指导
KEYWORDS_DOSING_CHANGE="自己.*加量|自行.*加量|加量.*可以吗|能不能.*加量|\
自己.*减量|自行.*减量|减量.*可以吗|能不能.*减量|\
自己.*换药|自行.*换药|换药.*可以吗|能不能.*换药|自己.*换成|想.*自己.*换成|\
自己.*停药|自行.*停药|停药.*可以吗|能不能.*停药|\
自行.*调整.*药|自己.*调整.*药量|我该.*吃多少.*药|\
加倍.*剂量|减半|把.*剂量.*改|改.*剂量|\
自己.*把.*药|把.*药.*减|把.*药.*加|自行.*加|自行.*减|\
自己.*加到|自己.*把.*加到|把.*从.*加到.*单位"

if echo "$QUESTION" | grep -qE "$KEYWORDS_DOSING_CHANGE"; then
  echo "out_of_scope:dosing_change"
  exit 0
fi

# ─── E 类：完全无关任务（应礼貌拒答）──────────────────────────
KEYWORDS_WRITING="作文|写一篇文章|帮我写文|帮我写篇|写代码|帮我编程|帮我写程序"
KEYWORDS_FINANCE="股票|基金|理财|投资建议|炒股"
KEYWORDS_WEATHER="天气怎么样|天气如何|天气预报|查天气|今天.*天气|明天.*天气"
KEYWORDS_COOKING="菜谱|烹饪方法|怎么做菜|做菜|食谱|红烧|清蒸|怎么烹饪|怎么做.*肉|怎么煮|炖.*汤"
KEYWORDS_TRANSLATE="翻译成英文|翻译成中文|请翻译|帮我翻译"
# 音乐/娱乐推荐（区别于"音乐疗法"：后者不含推荐意图+具体曲目，此处只拦截"推荐内容"类请求）
KEYWORDS_ENTERTAINMENT="推荐.*[首张支首].*音乐|推荐.*歌曲|推荐.*歌单|推荐.*播放列表|\
推荐.*电影|推荐.*电视剧|推荐.*影片|推荐.*影视|\
音乐.*推荐|歌曲.*推荐|好听的.*音乐|什么.*音乐.*好听"

if echo "$QUESTION" | grep -qE "$KEYWORDS_WRITING|$KEYWORDS_FINANCE|$KEYWORDS_WEATHER|$KEYWORDS_COOKING|$KEYWORDS_TRANSLATE|$KEYWORDS_ENTERTAINMENT"; then
  echo "out_of_scope:unrelated"
  exit 0
fi

# ─── 通过所有检测 → in_scope ──────────────────────────────────
echo "in_scope"
