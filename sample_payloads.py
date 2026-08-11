"""Sample request payloads, shaped exactly like the real request schemas (schemas/SCHEMAS.md).
Shared between test_engine.py and the Compare page's "Load sample" buttons.
"""

RECORD_EVALUATOR_PAYLOAD = {
    "content": {
        "الإنجاز": "طوّرت نظام حجز مواعيد إلكتروني قلل وقت الانتظار بنسبة 45%",
        "التأثير": "تحسين تجربة المرضى وتقليل الازدحام في العيادة",
    },
    "rubric_data": {
        "rubric_type": "الإنجاز والتأثير",
        "criteria": "الأداء (35%)، المساهمات (30%)، النمو (20%)، الانفتاح (15%)",
    },
    "record_id": "rec_00123",
}

REPORT_EVALUATOR_SINGLE_SUMMARY_PAYLOAD = {
    "summary_text": (
        "في إطار تعزيز الكفاءة التشغيلية والارتقاء بجودة الخدمات الصحية، طوّرت المرشحة نظاماً "
        "رقمياً لمتابعة المرضى بعد العمليات الجراحية، وأسهم ذلك في خفض نسبة إعادة المراجعة من "
        "15% إلى 6% خلال عام واحد. كما شاركت في برنامج متقدم لتحليل البيانات الصحية لدى معهد "
        "التحول الصحي الرقمي، وطبّقت ما تعلمته على تحسين تحليل بيانات العمليات الجراحية."
    ) * 2,  # clears the real 100-char minimum with margin
    "rubric_data": {
        "title": "جائزة التميز الوظيفي",
        "criteria": [
            {
                "name": "الإنجاز والتأثير",
                "weight": 35.0,
                "sub_dimension": "الأداء",
                "performance_levels": [
                    {"range": "80-100%", "min_percent": 80, "max_percent": 100, "description": "جهود تفوق التوقعات"},
                    {"range": "55-75%", "min_percent": 55, "max_percent": 75, "description": "جهود تستوفي التوقعات"},
                    {"range": "30-50%", "min_percent": 30, "max_percent": 50, "description": "جهود بسيطة"},
                    {"range": "5-25%", "min_percent": 5, "max_percent": 25, "description": "جهود بسيطة جداً"},
                ],
            },
            {
                "name": "الإنجاز والتأثير",
                "weight": 30.0,
                "sub_dimension": "المساهمات",
                "performance_levels": [
                    {"range": "80-100%", "min_percent": 80, "max_percent": 100, "description": "مساهمات استثنائية"},
                    {"range": "55-75%", "min_percent": 55, "max_percent": 75, "description": "مساهمات جيدة"},
                    {"range": "30-50%", "min_percent": 30, "max_percent": 50, "description": "مساهمات محدودة"},
                    {"range": "5-25%", "min_percent": 5, "max_percent": 25, "description": "مساهمات ضعيفة"},
                ],
            },
        ],
        "total_weight": 65.0,
    },
    "language": "ar",
}

ATTEMPT_COMPARATOR_PAYLOAD = {
    "attempts_data": {
        # NOTE: the inner key is "achievements", NOT "attempts" -- verified
        # directly against attempts_data.get("achievements", []) in
        # core/comparator.py, on every branch/generation checked (main and
        # core42-tests, azure-functions/ and azure-functions-unified/, both
        # identical). A caller sending "attempts" here would silently get
        # an empty achievements list -- title generation would never run,
        # and every item would fall back to generic "Input N" phrasing, not
        # because it failed, but because the code never sees anything to
        # title in the first place. "attempts_id" per item is harmless
        # extra data (ignored by the real code -- it only ever reads
        # "content" from each achievement dict).
        "achievements": [
            {
                "attempts_id": 1,
                "content": [
                    {
                        "المصدر / التحدي": "ضعف تعرض الكوادر الشابة للمهارات الجراحية المتقدمة",
                        "الأنشطة والمشاريع المستقبلية": "إنشاء برنامج محاكاة جراحية افتراضية باستخدام تقنية الواقع الافتراضي (VR)",
                        "تاريخ الإنجاز المتوقع": "يناير 2026",
                        "الأثر المتوقع": "رفع كفاءة الأطباء المقيمين بنسبة 50% وتقليل أخطاء المبتدئين داخل غرف العمليات",
                    }
                ],
            },
            {
                "attempts_id": 2,
                "content": [
                    {
                        "المصدر / التحدي": "نقص التوعية المجتمعية حول أهمية الكشف المبكر لبعض الأمراض الجراحية",
                        "الأنشطة والمشاريع المستقبلية": "إطلاق حملة توعوية مجتمعية رقمية وواقعية بعنوان \"فحص مبكر - تعافي أسرع\"",
                        "تاريخ الإنجاز المتوقع": "مارس 2026",
                        "الأثر المتوقع": "زيادة نسبة حالات الكشف المبكر بنسبة 25% وتقليل الحالات المتقدمة التي تتطلب تدخلاً معقداً",
                    }
                ],
            },
            {
                "attempts_id": 3,
                "content": [
                    {
                        "المصدر / التحدي": "تفاوت مخرجات الجراحة بين الفرق الجراحية المختلفة",
                        "الأنشطة والمشاريع المستقبلية": "إعداد دليل سريري موحد لإجراءات العمليات وفقاً لأفضل الممارسات العالمية المعتمدة",
                        "تاريخ الإنجاز المتوقع": "يوليو 2025",
                        "الأثر المتوقع": "توحيد الأداء بين الفرق وتقليل المضاعفات غير المتوقعة بنسبة 30%",
                    }
                ],
            },
        ],
    },
    "rubric_data": {
        "some_rubric_name": (
            "الموظف جهود تفوق التوقعات وتتمدى مهامه وأهدافه الشخصية لتحقيق إنجازات متميزة على مدى مسيرته المهنية "
            "لوحدته التنظيمية مقارنة بالوحدات الأخرى داخل الجهة وخارجها.\n\n"
            "يحرص الموظف على قياس الأداء بشكل مستمر للتأكد من مدى تحقيق الأهداف سواء الشخصية أو المتعلقة بجهة "
            "العمل، وكذلك على تحليل البيانات واستقراء المعلومات لصنع القرارات الذكية وبشكل استباقي ووضع خطط "
            "المخاطر والسيناريوهات البديلة بهدف الاستفادة من الفرص الحالية والمستقبلية لمواجهة التحديات وصنع "
            "وتحقيق إنجازات فارقة، ويقوم بشكل مستمر بتقديم مساهمات تهدف لتعزيز ثقافة الجودة والتميز داخل بيئة العمل.\n\n"
            "يُظهر الموظف نمواً متصاعداً في الأداء وتحقيق أهدافه الشخصية وتحقيق مؤشرات وأهداف وحدته التنظيمية، "
            "كما تزداد حجم ونوعية الإنجازات المتفردة التي يقدمها لوحدته أو للجهة سنوياً. أسهمت الإنجازات التي "
            "قام بها الموظف في تحقيق تميز أداء جهة العمل وخفض التكاليف وتقليل الوقت والجهد المستغرق في أداء "
            "العمليات والخدمات مما يفوق المستهدفات المحددة سنوياً على مستوى الجهة. كما تساهم جهوده والمبادرات "
            "التي يقوم بها في زيادة نسبة السعادة لدى المتعاملين لتعزيز أداء جهته.\n\n"
            "يقوم الموظف بمقارنة أدائه وأداء الوحدة التنظيمية بأداء الوحدات الأخرى داخلياً وخارجياً، كما يستفيد "
            "من المقارنات وأفضل الممارسات المحلية والإقليمية والعالمية لمواجهة التحديات واتخاذ القرارات الذكية "
            "ووضع الخطط المستقبلية وصنع الإنجازات الفارقة له شخصياً ولجهة عمله يضمن لها التفوق على مثيلاتها."
        ),
    },
    # No "model" field: model selection in Prompt Lab always happens via the
    # Side A/B dropdowns in the UI, never from the payload text -- a "model"
    # key here would be silently unused by adapt_attempt_comparator() and
    # would misleadingly suggest editing it changes anything.
}

PILLAR_SUMMARIZER_PAYLOAD = {
    # candidate_info is accepted by the real request model and echoed back in
    # the HTTP response, but never reaches any prompt -- the summary is built
    # purely from pillar_data[].rows. Included here so the payload matches the
    # real request shape; editing it will not change a single generated word.
    "candidate_info": {
        "name": "د. سارة المنصوري",
        "position": "استشاري جراحة عامة",
        "organization": "مدينة الشيخ خليفة الطبية",
    },
    "pillar_data": [
        {
            "pillar_name": "الإنجاز والتأثير",
            "rows": [
                {
                    "الإنجاز": "تأسيس وحدة الجراحة اليومية وتشغيلها بالكامل",
                    "الوصف": "قدت فريقاً متعدد التخصصات لتأسيس وحدة الجراحة اليومية، وأعددت مسارات العمل السريرية ومعايير اختيار الحالات",
                    "الأثر": "خفض متوسط مدة بقاء المريض من 3 أيام إلى 8 ساعات، ورفع عدد العمليات المنجزة أسبوعياً من 24 إلى 41 عملية",
                    "السنة": "2024",
                },
                {
                    "الإنجاز": "برنامج تقليل عدوى موضع الجراحة",
                    "الوصف": "طبقت حزمة إجراءات وقائية معيارية وأطلقت لوحة متابعة شهرية لمعدلات العدوى على مستوى الأقسام الجراحية",
                    "الأثر": "انخفاض معدل عدوى موضع الجراحة من 4.2% إلى 1.1% خلال 18 شهراً",
                    "السنة": "2023",
                },
                {
                    "الإنجاز": "اعتماد الزمالة السريرية لتدريب الأطباء المقيمين",
                    "الوصف": "أعددت ملف الاعتماد الكامل ونسقت مع الهيئة المانحة، وصممت خطة التدريب ومصفوفة الكفاءات",
                    "الأثر": "اعتماد البرنامج لخمس سنوات وقبول أول دفعة من ستة أطباء مقيمين",
                    "السنة": "2025",
                },
                {
                    # A deliberately sparse row: the two empty-ish values below are
                    # dropped by the data_text builder's own filter (falsy / "nan"),
                    # exactly as production drops empty Excel cells. Kept in the
                    # sample so that filter is exercised by default.
                    "الإنجاز": "مبادرة الجراحة الآمنة",
                    "الوصف": "",
                    "الأثر": "nan",
                    "السنة": "2024",
                },
            ],
        }
    ],
    # 575 is the real request-model default. The loop clamps anything above 600,
    # so raising this past 600 in the UI is a good way to see that clamp reported.
    "target_word_count": 575,
    "language": "ar",
    # No "model" field, for the same reason as Attempt Comparator above.
}
