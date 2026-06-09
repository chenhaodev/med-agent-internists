#!/usr/bin/env bash
# router.sh — 将问题分类到 专科:疾病 标签（最多 2 个）
# 用法：./bin/router.sh "问题文本"
# 输出：空格分隔的 specialty:disease 标签，例如 "cardiology:hypertension"
#
# 专科列表（与教材部分对齐）：
#   cardiology      心血管（第二部分）
#   endocrine       内分泌代谢（第五部分）
#   respiratory     呼吸（第三部分）
#   digestive       消化含肝（第四部分）
#   renal           肾（第六部分）
#   hematology      血液（第七部分）
#   infectious      感染（第八部分）
#   rheumatology    风湿骨（第十部分）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

if [[ $# -ge 1 ]]; then
  QUESTION="$1"
else
  QUESTION="$(cat)"
fi

if [[ -z "$QUESTION" ]]; then
  echo "cardiology:general"
  exit 0
fi

# ─── 关键词路由表（专科:疾病 粒度）──────────────────────────
# v2：扩展至全书 18 专科

# 心血管
KW_CARDIOLOGY_HYPERTENSION="高血压|血压高|降压|血压控制|收缩压|舒张压|高压|低压|降压药|自测血压|家庭血压|家里测血压|血压监测|白大衣高血压|诊室外血压"
KW_CARDIOLOGY_HEART_FAILURE="心衰|心力衰竭|心功能不全|射血分数|BNP|NT-proBNP|喘|端坐呼吸|下肢水肿.*心"
KW_CARDIOLOGY_CAD="冠心病|冠状动脉|心绞痛|心肌梗死|心梗|胸痛|心肌缺血|稳定型|不稳定型|戒烟|尼古丁替代|尼古丁贴片|戒烟方法|帮助戒烟"
KW_CARDIOLOGY_ARRHYTHMIA="心律失常|房颤|心房颤动|早搏|室速|室颤|心动过速|心动过缓|心跳不规则|心跳乱"
KW_CARDIOLOGY_VALVE="心脏瓣膜|瓣膜病|主动脉瓣|二尖瓣|瓣膜狭窄|瓣膜反流|瓣膜关闭不全|主动脉瓣狭窄|二尖瓣脱垂|换瓣|TAVI|TAVR|心脏瓣膜置换"
KW_CARDIOLOGY_PERICARDIAL="心包炎|心包积液|心脏压塞|缩窄性心包炎|心包"
KW_CARDIOLOGY_CHD="先天性心脏病|先心病|房间隔缺损|室间隔缺损|动脉导管未闭|法洛四联症|艾森门格|Eisenmenger|主动脉缩窄"
KW_CARDIOLOGY_OTHER="心脏肿瘤|心脏黏液瘤|主动脉夹层|夹层动脉瘤"
KW_CARDIOLOGY_GENERAL="心脏病|心脏|心血管|心脏功能|心电图"

# 内分泌代谢
KW_ENDOCRINE_DIABETES="糖尿病|血糖|高血糖|低血糖|胰岛素|降糖药|糖化血红蛋白|HbA1c|空腹血糖|餐后血糖|二甲双胍|胰岛"
KW_ENDOCRINE_THYROID="甲状腺|甲亢|甲减|甲状腺功能|促甲状腺|T3|T4|TSH|甲状腺素|甲状腺结节"
KW_ENDOCRINE_OBESITY="肥胖|减重|减肥|BMI|体重超标|代谢综合征"
KW_ENDOCRINE_GOUT="痛风|高尿酸|尿酸|尿酸盐|别嘌醇|非布司他"
KW_ENDOCRINE_LIPID="血脂|高血脂|胆固醇|LDL|HDL|甘油三酯|他汀|降脂"
KW_ENDOCRINE_PITUITARY="垂体|垂体瘤|垂体腺瘤|泌乳素瘤|催乳素|高泌乳素|肢端肥大症|IGF-1|生长激素|库欣病|垂体性"
KW_ENDOCRINE_ADRENAL="肾上腺|艾迪生|Addison|皮质醇增多症|库欣综合征|肾上腺皮质功能|原发性醛固酮增多症|肾上腺危象"
KW_ENDOCRINE_NUTRITION="营养支持|肠内营养|肠外营养|TPN|静脉营养|再喂养综合征|ICU.*营养|营养不良.*支持"
KW_ENDOCRINE_GENERAL="内分泌|激素|代谢|胰腺"

# 呼吸
KW_RESPIRATORY_COPD="慢阻肺|COPD|慢性阻塞性肺疾病|肺气肿|慢性支气管炎|气流受限|肺功能下降"
KW_RESPIRATORY_ASTHMA="哮喘|支气管哮喘|气喘|喘息|过敏性哮喘|哮喘发作|吸入激素"
KW_RESPIRATORY_PNEUMONIA="肺炎|肺部感染|社区获得性肺炎|医院获得性|肺炎球菌|肺部阴影"
KW_RESPIRATORY_ILD="间质性肺病|ILD|肺纤维化|特发性肺纤维化|IPF|结节病|过敏性肺炎|肺间质病变"
KW_RESPIRATORY_SLEEP="睡眠呼吸暂停|OSAHS|OSA|打鼾.*缺氧|阻塞性睡眠.*呼吸|CPAP.*呼吸|呼吸暂停综合征"
KW_RESPIRATORY_PLEURAL="胸腔积液|胸水|气胸|胸膜炎|脓胸|渗出液|漏出液|Light.*标准|张力性气胸|胸膜"
KW_RESPIRATORY_LUNG_TUMOR="肺癌|肺部肿瘤|非小细胞肺癌|NSCLC|小细胞肺癌|SCLC|肺腺癌|肺鳞癌|EGFR.*肺|肺结节|肺部.{0,12}结节|肺.{0,6}结节|LDCT.*筛查|肺癌筛查"
KW_RESPIRATORY_CRITICAL="ARDS|急性呼吸窘迫综合征|机械通气.*ICU|气管插管.*通气|6ml.*公斤.*通气"
KW_RESPIRATORY_GENERAL="咳嗽|咳痰|呼吸困难|气短|气急|肺|呼吸|支气管|氧饱和度"

# 消化含肝
KW_DIGESTIVE_HEPATITIS="病毒性肝炎|乙型肝炎|丙型肝炎|乙肝|丙肝|HBV|HCV|HBsAg|大三阳|小三阳|抗病毒治疗|自身免疫性肝炎|肝炎疫苗|乙肝疫苗|乙肝携带者|慢性肝炎|肝炎病毒|甲肝|戊肝"
KW_DIGESTIVE_LIVER="肝硬化|肝功能|肝纤维化|转氨酶|ALT|AST|胆红素|肝癌风险"
KW_DIGESTIVE_IBD="炎症性肠病|克罗恩|溃疡性结肠炎|肠炎|肠道炎症|IBD"
KW_DIGESTIVE_ESOPHAGUS="食管|反流性食管炎|Barrett食管|Barrett|巴雷特|吞咽困难.*食管|烧心.*食管|GERD|胃食管反流病|反流.*食管|食管动力|贲门失弛缓|胃食管反流"
KW_DIGESTIVE_JAUNDICE="黄疸|高胆红素血症|皮肤发黄|眼黄|Gilbert综合征|梗阻性黄疸|溶血性黄疸|肝细胞性黄疸|胆汁淤积"
KW_DIGESTIVE_GI="胃炎|消化性溃疡|胃溃疡|十二指肠溃疡|幽门螺杆菌|HP感染|消化不良|上消化道出血|消化道出血|呕血|黑便"
KW_DIGESTIVE_BILIARY="胆囊结石|胆结石|胆囊炎|急性胆囊炎|胆道|胆绞痛|胆石症|胆总管结石|胆囊息肉|胆管炎|胆囊"
KW_DIGESTIVE_PANCREAS="胰腺炎|急性胰腺炎|慢性胰腺炎|胰腺|胰腺外分泌|淀粉酶|脂肪酶.*胰"
KW_DIGESTIVE_GENERAL="消化|肠道|胃肠|腹泻|便秘|腹痛|腹胀|大便|肠胃"

# 肾
KW_RENAL_CKD="慢性肾病|CKD|慢性肾功能不全|肾功能不全|肾功能不好|肾功能减退|肌酐升高|肾小球滤过率|eGFR|蛋白尿"
KW_RENAL_NEPHRITIS="肾炎|肾小球肾炎|IgA肾病|膜性肾病"
KW_RENAL_AKI="急性肾损伤|AKI|急性肾衰|急性肾功能|肌酐突然|少尿.*肾|无尿.*肾|肾功能突然"
KW_RENAL_ELECTROLYTES="高钾血症|低钾血症|高钠血症|低钠血症|低钠|高钾|电解质紊乱|水电解质|低镁|高钙|低钙.*血|低磷"
KW_RENAL_VASCULAR="肾血管性高血压|肾动脉狭窄|TTP|溶血尿毒综合征|HUS|血栓性血小板减少性紫癜|硬皮病肾|肾血管"
KW_RENAL_GENERAL="肾|尿蛋白|血尿|肾功能|尿毒症|肾脏病"

# 血液
KW_HEMATOLOGY_ANEMIA="贫血|血红蛋白低|缺铁性贫血|恶性贫血|溶血性贫血|地中海贫血|再生障碍性贫血"
KW_HEMATOLOGY_BLEEDING="ITP|免疫性血小板减少|血小板减少性紫癜|血友病|出血性疾病|凝血因子缺乏|血小板减少.*出血|皮肤瘀斑.*血小板"
KW_HEMATOLOGY_THROMBOSIS="肺栓塞|肺血栓|DVT|深静脉血栓|静脉血栓|血栓形成|抗凝治疗|华法林.*血栓|血栓.*预防|易栓|低分子肝素.*血栓|术后.*腿肿|手术后.*腿肿|手术后.*腿|腿肿.*手术|术后.*水肿.*腿|术后血栓|手术后血栓"
KW_HEMATOLOGY_LYMPHOMA="淋巴瘤|霍奇金淋巴瘤|非霍奇金淋巴瘤|淋巴结肿大.*恶性|淋巴细胞性白血病|CLL|淋巴细胞增多"
KW_HEMATOLOGY_MYELOID="骨髓增殖|CML|慢性髓性白血病|费城染色体|伊马替尼|BCR.*ABL|JAK2.*突变|真性红细胞增多症|血小板增多症|骨髓纤维化|鲁索利替尼|ruxolitinib"
KW_HEMATOLOGY_GENERAL="血液病|白血病|骨髓瘤|血小板减少|白细胞低|血细胞|骨髓"

# 感染
KW_INFECTIOUS_HIV="HIV|艾滋|人类免疫缺陷病毒|获得性免疫缺陷综合征|AIDS|抗病毒治疗.*HIV|CD4"
KW_INFECTIOUS_UTI="尿路感染|膀胱炎|肾盂肾炎|尿痛|尿频.*感染|尿道炎|泌尿道感染|菌尿"
KW_INFECTIOUS_SEPSIS="脓毒症|败血症|感染性休克|菌血症|脓毒血症|多器官衰竭.*感染|脓毒"
KW_INFECTIOUS_CNS="脑膜炎|脑炎|颅内感染|中枢神经感染|细菌性脑膜炎|病毒性脑炎|脑脓肿"
KW_INFECTIOUS_FEVER="不明原因发热|FUO|发热待查|疟疾|伤寒|布鲁氏菌|旅行.*发热|旅行.*发烧|回国.*发热|回国.*发烧|回来.*发烧|境外.*发烧|热带.*发热|东南亚.*发"
KW_INFECTIOUS_LOWER_RESP="社区获得性肺炎|CAP|CURB-65|军团菌|支原体肺炎|非典型肺炎.*抗生素|肺炎.*抗菌药物选择"
KW_INFECTIOUS_SKIN="皮肤软组织感染|蜂窝织炎|坏死性筋膜炎|MRSA.*皮肤|CA-MRSA|皮肤脓肿.*感染|链球菌.*皮肤|坏死性感染"
KW_INFECTIOUS_GENERAL="感染|发烧|发热|细菌感染|病毒感染|抗生素|抗感染|结核|TB|梅毒"

# 风湿骨
KW_RHEUMATOLOGY_RA="类风湿|类风湿关节炎|RA|关节肿胀|晨僵|抗CCP|类风湿因子|RF"
KW_RHEUMATOLOGY_SLE="系统性红斑狼疮|SLE|蝴蝶斑|狼疮肾炎"
KW_RHEUMATOLOGY_OSTEOPOROSIS="骨质疏松|骨密度|骨折风险|钙|维生素D|双膦酸盐|T值"
KW_RHEUMATOLOGY_OA="骨关节炎|退行性关节|膝关节退变|关节退化.*老年|关节磨损|骨赘|膝关节.*关节炎"
KW_RHEUMATOLOGY_SPA="强直性脊柱炎|脊柱关节炎|SpA|HLA-B27|反应性关节炎|银屑病关节炎|炎性腰背痛|骶髂关节炎|竹节样变|TNF-α.*脊柱"
KW_RHEUMATOLOGY_SSC="系统性硬化|硬皮病|CREST综合征|雷诺现象.*硬皮|硬皮病肾危象|抗Scl-70|指端硬化|硬皮"
KW_RHEUMATOLOGY_VASCULITIS="血管炎|GPA|韦格纳肉芽肿|显微镜下多血管炎|MPA|ANCA相关|cANCA|pANCA|巨细胞动脉炎|颞动脉炎|大动脉炎|Takayasu|嗜酸性肉芽肿性多血管炎"
KW_RHEUMATOLOGY_GENERAL="风湿|关节炎|关节痛|痛风性关节|干燥综合征"

# 肿瘤（科普级别，不含化疗方案）
KW_ONCOLOGY_LUNG="肺癌|肺部肿瘤|非小细胞肺癌|小细胞肺癌|肺结节.*恶性"
KW_ONCOLOGY_GI="肠癌|结直肠癌|胃癌|食管癌|胰腺癌|肝癌"
KW_ONCOLOGY_BREAST="乳腺癌|乳腺肿瘤"
KW_ONCOLOGY_LYMPHOMA="淋巴瘤|霍奇金淋巴瘤|非霍奇金"
KW_ONCOLOGY_COMPLICATIONS="化疗.*恶心|化疗.*呕吐|化疗后.*恶心|化疗后.*疲乏|靶向治疗副作用|化疗副作用|放疗副作用"
KW_ONCOLOGY_GENERAL="肿瘤|癌症|癌|肿瘤营养|肿瘤患者.*饮食|癌症患者"

# 神经内科
KW_NEUROLOGY_STROKE="脑卒中|脑梗|脑出血|中风|偏瘫|失语|吞咽困难.*脑|脑梗康复|脑血管|缺血性卒中|溶栓|rt-PA|阿替普酶|时间窗.*溶栓|溶栓.*时间窗|卒中.*适应证|卒中.*抗凝|脑卒中.*管理"
KW_NEUROLOGY_PARKINSON="帕金森|帕金森病|震颤|运动迟缓|肌强直|帕金森康复"
KW_NEUROLOGY_DEMENTIA="痴呆|阿尔茨海默|老年痴呆|记忆障碍|认知障碍|血管性痴呆"
KW_NEUROLOGY_EPILEPSY="癫痫|癫痫发作|抗癫痫|惊厥"
KW_NEUROLOGY_HEADACHE="偏头痛|头痛|紧张型头痛|丛集性头痛"
KW_NEUROLOGY_SLEEP="失眠|睡眠障碍|睡不着|入睡困难"
KW_NEUROLOGY_GENERAL="神经|肌无力|麻木|感觉异常|眩晕|头晕|意识障碍|运动障碍|神经病变"

# 精神/心理（归入神经科路由）
KW_NEUROLOGY_PSYCH="抑郁症|抑郁|焦虑症|焦虑|双相情感障碍|躁郁症|精神分裂|心理健康|情绪障碍"

# 妇科健康
KW_WOMENS_HEALTH="月经不调|痛经|更年期|绝经|围绝经期|多囊卵巢|宫颈|卵巢|乳腺健康|女性健康|骨盆底"

# 男性健康
KW_MENS_HEALTH="前列腺|ED|勃起功能|男性健康|睾酮|男性性功能|性功能|前列腺增生"

# 骨代谢矿物质
KW_BONE_MINERAL="骨代谢|维生素D缺乏|甲状旁腺|钙代谢|磷代谢|代谢性骨病|佝偻病|骨软化"

# 老年医学
KW_GERIATRICS="老年患者|老年人用药|老年综合评估|衰弱|跌倒|预防跌倒|老人.*跌倒|老年痴呆.*管理|多重用药|药物相互作用.*老|老.*药物相互作用|多种药物|老人.*吃药|用药.*老年"

# 姑息治疗
KW_PALLIATIVE="姑息治疗|缓和医疗|临终关怀|安宁疗护|终末期|疼痛控制.*癌症|生命末期|癌.*疼痛|癌痛|晚期.*疼痛|肿瘤.*疼痛|止痛.*癌|镇痛.*癌"

# 物质滥用
KW_SUBSTANCE="酗酒|酒精依赖|戒酒|酒精戒断|戒断综合征|酒精性.*肝|药物滥用|成瘾|苯二氮䓬.*戒酒|苯二氮䓬.*酒精|酒精.*苯二氮|CIWA|震颤谵妄|酒精.*评估"

# 围术期
KW_PERIOPERATIVE="术前|术前评估|围手术期|围术期管理|手术前.*内科|手术风险.*内科|术前.*停药|术前.*降压|术前.*降糖"

# ─── 匹配逻辑 ────────────────────────────────────────────────
matched=()

check() {
  local tag="$1" pattern="$2"
  if echo "$QUESTION" | grep -qE "$pattern"; then
    matched+=("$tag")
  fi
}

# 按疾病粒度检查（越具体越先检查）
# 围手术期为跨专科优先标签：术前停药等问题应优先于具体病种路由
check "perioperative:periop_management" "$KW_PERIOPERATIVE"

check "cardiology:hypertension"    "$KW_CARDIOLOGY_HYPERTENSION"
check "cardiology:heart_failure"   "$KW_CARDIOLOGY_HEART_FAILURE"
check "cardiology:cad"             "$KW_CARDIOLOGY_CAD"
check "cardiology:arrhythmia"      "$KW_CARDIOLOGY_ARRHYTHMIA"
check "cardiology:valve_disease"   "$KW_CARDIOLOGY_VALVE"
check "cardiology:pericardial"     "$KW_CARDIOLOGY_PERICARDIAL"
check "cardiology:congenital_hd"   "$KW_CARDIOLOGY_CHD"
check "cardiology:other_cardiac"   "$KW_CARDIOLOGY_OTHER"

check "endocrine:diabetes_t2"      "$KW_ENDOCRINE_DIABETES"
check "endocrine:thyroid"          "$KW_ENDOCRINE_THYROID"
check "endocrine:obesity"          "$KW_ENDOCRINE_OBESITY"
check "endocrine:gout"             "$KW_ENDOCRINE_GOUT"
check "endocrine:dyslipidemia"     "$KW_ENDOCRINE_LIPID"
check "endocrine:pituitary"        "$KW_ENDOCRINE_PITUITARY"
check "endocrine:adrenal"          "$KW_ENDOCRINE_ADRENAL"
check "endocrine:nutrition"        "$KW_ENDOCRINE_NUTRITION"

check "respiratory:copd"           "$KW_RESPIRATORY_COPD"
check "respiratory:asthma"         "$KW_RESPIRATORY_ASTHMA"
check "respiratory:pneumonia"      "$KW_RESPIRATORY_PNEUMONIA"
check "respiratory:ild"            "$KW_RESPIRATORY_ILD"
check "respiratory:sleep_breathing" "$KW_RESPIRATORY_SLEEP"
check "respiratory:pleural"        "$KW_RESPIRATORY_PLEURAL"
check "respiratory:lung_tumor"     "$KW_RESPIRATORY_LUNG_TUMOR"
check "respiratory:critical_care"  "$KW_RESPIRATORY_CRITICAL"

check "digestive:hepatitis"        "$KW_DIGESTIVE_HEPATITIS"
check "digestive:liver"            "$KW_DIGESTIVE_LIVER"
check "digestive:ibd"              "$KW_DIGESTIVE_IBD"
check "digestive:esophagus"        "$KW_DIGESTIVE_ESOPHAGUS"
check "digestive:gi"               "$KW_DIGESTIVE_GI"
check "digestive:biliary"          "$KW_DIGESTIVE_BILIARY"
check "digestive:pancreas"         "$KW_DIGESTIVE_PANCREAS"
check "digestive:jaundice"         "$KW_DIGESTIVE_JAUNDICE"

check "renal:ckd"                  "$KW_RENAL_CKD"
check "renal:nephritis"            "$KW_RENAL_NEPHRITIS"
check "renal:aki"                  "$KW_RENAL_AKI"
check "renal:electrolytes"         "$KW_RENAL_ELECTROLYTES"
check "renal:renal_vascular"       "$KW_RENAL_VASCULAR"

check "hematology:anemia"          "$KW_HEMATOLOGY_ANEMIA"
check "hematology:bleeding_disorders" "$KW_HEMATOLOGY_BLEEDING"
check "hematology:thrombosis"      "$KW_HEMATOLOGY_THROMBOSIS"
check "hematology:lymphocyte"      "$KW_HEMATOLOGY_LYMPHOMA"
check "hematology:myeloid_clonal"  "$KW_HEMATOLOGY_MYELOID"

check "infectious:hiv"             "$KW_INFECTIOUS_HIV"
check "infectious:uti"             "$KW_INFECTIOUS_UTI"
check "infectious:sepsis"          "$KW_INFECTIOUS_SEPSIS"
check "infectious:cns_infection"   "$KW_INFECTIOUS_CNS"
check "infectious:fever"           "$KW_INFECTIOUS_FEVER"
check "infectious:lower_resp_infection" "$KW_INFECTIOUS_LOWER_RESP"
check "infectious:skin_soft_tissue" "$KW_INFECTIOUS_SKIN"
check "infectious:general"         "$KW_INFECTIOUS_GENERAL"

check "rheumatology:ra"            "$KW_RHEUMATOLOGY_RA"
check "rheumatology:sle"           "$KW_RHEUMATOLOGY_SLE"
check "rheumatology:osteoporosis"  "$KW_RHEUMATOLOGY_OSTEOPOROSIS"
check "rheumatology:oa"            "$KW_RHEUMATOLOGY_OA"
check "rheumatology:spa"           "$KW_RHEUMATOLOGY_SPA"
check "rheumatology:systemic_sclerosis" "$KW_RHEUMATOLOGY_SSC"
check "rheumatology:vasculitis"    "$KW_RHEUMATOLOGY_VASCULITIS"

# 肿瘤
check "oncology:tumor_complications" "$KW_ONCOLOGY_COMPLICATIONS"
check "oncology:lung_cancer"       "$KW_ONCOLOGY_LUNG"
check "oncology:gi_cancer"         "$KW_ONCOLOGY_GI"
check "oncology:breast_cancer"     "$KW_ONCOLOGY_BREAST"
check "oncology:lymphocyte"        "$KW_ONCOLOGY_LYMPHOMA"

# 神经（含精神科症状）
check "neurology:stroke"           "$KW_NEUROLOGY_STROKE"
check "neurology:movement_disorders" "$KW_NEUROLOGY_PARKINSON"
check "neurology:dementia"         "$KW_NEUROLOGY_DEMENTIA"
check "neurology:epilepsy"         "$KW_NEUROLOGY_EPILEPSY"
check "neurology:headache_pain"    "$KW_NEUROLOGY_HEADACHE"
check "neurology:sleep_disorders"  "$KW_NEUROLOGY_SLEEP"
check "neurology:mood_behavior"    "$KW_NEUROLOGY_PSYCH"

# 其他专科
check "womens_health:womens_health" "$KW_WOMENS_HEALTH"
check "mens_health:mens_health"     "$KW_MENS_HEALTH"
check "bone_mineral:mineral_disorders" "$KW_BONE_MINERAL"
check "geriatrics:elderly_care"     "$KW_GERIATRICS"
check "palliative:palliative_care"  "$KW_PALLIATIVE"
check "substance_use:alcohol_drugs" "$KW_SUBSTANCE"

# 专科级兜底（如果疾病级未命中）
if [[ ${#matched[@]} -eq 0 ]]; then
  check "cardiology:general"    "$KW_CARDIOLOGY_GENERAL"
  check "endocrine:general"     "$KW_ENDOCRINE_GENERAL"
  check "respiratory:general"   "$KW_RESPIRATORY_GENERAL"
  check "digestive:general"     "$KW_DIGESTIVE_GENERAL"
  check "renal:general"         "$KW_RENAL_GENERAL"
  check "hematology:general"    "$KW_HEMATOLOGY_GENERAL"
  check "rheumatology:general"  "$KW_RHEUMATOLOGY_GENERAL"
  check "oncology:general"      "$KW_ONCOLOGY_GENERAL"
  check "neurology:general"     "$KW_NEUROLOGY_GENERAL"
fi

# ─── DeepSeek 兜底分类 ──────────────────────────────────────
if [[ ${#matched[@]} -eq 0 ]]; then
  if [[ -f "$ROOT_DIR/.env" ]]; then
    # set -a：自动导出 source 进来的变量，使下方 python3 -c 子进程
    # 能通过 os.environ 读到 DEEPSEEK_MODEL / DEEPSEEK_API_KEY
    set -a
    source "$ROOT_DIR/.env" 2>/dev/null || true
    set +a
  fi

  if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
    DOMAINS_LIST="cardiology:hypertension, cardiology:heart_failure, cardiology:cad, cardiology:arrhythmia, cardiology:valve_disease, endocrine:diabetes_t2, endocrine:thyroid, endocrine:dyslipidemia, endocrine:gout, endocrine:obesity, respiratory:copd, respiratory:asthma, respiratory:pneumonia, respiratory:ild, respiratory:pulmonary_vascular, digestive:liver, digestive:gi, digestive:ibd, digestive:hepatitis, digestive:biliary, digestive:pancreas, renal:ckd, renal:nephritis, renal:aki, renal:electrolytes, hematology:anemia, hematology:bleeding_disorders, hematology:thrombosis, hematology:lymphocyte, infectious:general, infectious:hiv, infectious:uti, infectious:sepsis, infectious:cns_infection, rheumatology:ra, rheumatology:sle, rheumatology:osteoporosis, rheumatology:oa, oncology:lung_cancer, oncology:gi_cancer, oncology:breast_cancer, oncology:tumor_complications, neurology:stroke, neurology:movement_disorders, neurology:dementia, neurology:epilepsy, neurology:headache_pain, neurology:sleep_disorders, neurology:mood_behavior, womens_health:womens_health, mens_health:mens_health, geriatrics:elderly_care, palliative:palliative_care, substance_use:alcohol_drugs, perioperative:periop_management"

    CLASSIFY_PAYLOAD=$(python3 -c "
import json, os, sys
question = sys.argv[1]
domains = sys.argv[2]
payload = {
  'model': os.environ.get('DEEPSEEK_MODEL', 'deepseek-v4-flash'),
  'temperature': 0,
  'max_tokens': 40,
  'messages': [
    {'role': 'system', 'content': f'你是一个医学分类器。从以下专科:疾病标签中选出1-2个最匹配的，只输出标签，用空格分隔，不要其他文字。\\n标签：{domains}'},
    {'role': 'user', 'content': question}
  ]
}
print(json.dumps(payload))
" "$QUESTION" "$DOMAINS_LIST" 2>/dev/null)

    if [[ -n "$CLASSIFY_PAYLOAD" ]]; then
      CLASSIFIED=$(echo "$CLASSIFY_PAYLOAD" | "$SCRIPT_DIR/call_deepseek.sh" 2>/dev/null | tr -s ' ' | xargs) || true
      VALID_TAGS="cardiology:hypertension cardiology:heart_failure cardiology:cad cardiology:arrhythmia cardiology:valve_disease cardiology:pericardial cardiology:congenital_hd cardiology:other_cardiac cardiology:general endocrine:diabetes_t2 endocrine:thyroid endocrine:obesity endocrine:gout endocrine:dyslipidemia endocrine:pituitary endocrine:adrenal endocrine:nutrition endocrine:general respiratory:copd respiratory:asthma respiratory:pneumonia respiratory:ild respiratory:pulmonary_vascular respiratory:sleep_breathing respiratory:pleural respiratory:lung_tumor respiratory:critical_care respiratory:general digestive:liver digestive:ibd digestive:gi digestive:hepatitis digestive:esophagus digestive:pancreas digestive:biliary digestive:jaundice digestive:general renal:ckd renal:nephritis renal:aki renal:electrolytes renal:renal_vascular renal:general hematology:anemia hematology:myeloid_clonal hematology:lymphocyte hematology:bleeding_disorders hematology:thrombosis hematology:general infectious:fever infectious:sepsis infectious:hiv infectious:uti infectious:lower_resp_infection infectious:cns_infection infectious:skin_soft_tissue infectious:general rheumatology:ra rheumatology:sle rheumatology:osteoporosis rheumatology:oa rheumatology:vasculitis rheumatology:spa rheumatology:systemic_sclerosis rheumatology:general oncology:lung_cancer oncology:gi_cancer oncology:breast_cancer oncology:gu_cancer oncology:other_solid_tumors oncology:tumor_complications oncology:tumor_treatment_principles oncology:general neurology:stroke neurology:movement_disorders neurology:dementia neurology:epilepsy neurology:headache_pain neurology:sleep_disorders neurology:mood_behavior neurology:dizziness neurology:consciousness neurology:general womens_health:womens_health mens_health:mens_health bone_mineral:mineral_disorders bone_mineral:metabolic_bone geriatrics:elderly_care palliative:palliative_care substance_use:alcohol_drugs perioperative:periop_management"
      VALID_RESULT=""
      for d in $CLASSIFIED; do
        if echo "$VALID_TAGS" | grep -qw "$d"; then
          VALID_RESULT="$VALID_RESULT $d"
        fi
      done
      VALID_RESULT=$(echo "$VALID_RESULT" | xargs)
      if [[ -n "$VALID_RESULT" ]]; then
        echo "$VALID_RESULT"
        exit 0
      fi
    fi
  fi

  echo "cardiology:general"
  exit 0
fi

# 最多保留 2 个标签
if [[ ${#matched[@]} -gt 2 ]]; then
  matched=("${matched[@]:0:2}")
fi

echo "${matched[*]}"
