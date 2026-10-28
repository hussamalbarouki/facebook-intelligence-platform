# تشغيل Facebook Intelligence Platform على Coolify

## 1. إنشاء مستودع GitHub
افتح https://github.com/new وأنشئ مستودعاً **Public** باسم `facebook-intelligence-platform`، بدون أسرار أو Cookies.
ارفع محتويات هذا المشروع إلى الفرع `main` (وليس ملف ZIP ذاته). ينشر GitHub Actions صورة Docker بعد نجاح الاختبارات والبناء. يمكنك أيضاً استعمال مشروع Compose من المستودع مباشرة دون انتظار GHCR.

## 2. إعداد Coolify
- New Resource → Public Repository.
- أدخل رابط مستودعك الفعلي بعد إنشائه.
- Build Pack: **Docker Compose**.
- Compose file: `compose.yaml`.
- Environment Variables: `ADMIN_PASSWORD`, `APP_SECRET`, `POSTGRES_PASSWORD` (قيم عشوائية قوية وثابتة).
- عيّن HTTPS domain على خدمة `platform` والمنفذ الداخلي `8000`، دون إتاحة PostgreSQL للعامة.
- Deploy.

## 3. التحقق
- افتح `https://DOMAIN/healthz`، وتأكد من `database=ok` و`engine_binary=ready`.
- افتح `https://DOMAIN/admin`، وسجل الدخول بـ `ADMIN_PASSWORD`.
- من تبويب «حسابات فيسبوك»، أضف حساباً عبر المتصفح أو قيمتي جلسة `c_user` و`xs`.
- من «مفاتيح API»، أنشئ مفتاحاً، وانسخه فوراً لأنه لا يظهر ثانية.
- افتح `https://DOMAIN/docs`، واضغط **Authorize** وأدخل المفتاح، ثم **Try it out**.

## 4. أول اختبار
`GET /v1/read/page?ref=nasa` مع `X-API-Key: YOUR_KEY`.
بعد نجاحه جرّب تعليقات منشور عام، ثم جرّب `reaction-identities` من Jobs مع الحساب. **لا تفترض أن أي قائمة أسماء متفاعلين كاملة حتى يثبت ذلك.**

## تنبيه
استخراج أسماء المتفاعلين عبر Playwright تجريبي، ولا يوجد ضمان للوصول إلى كل الأسماء. لا يدعم المشروع تجاوز القيود أو تحديات التحقق. لا تضع كلمات المرور أو ملفات الجلسة في GitHub، ولا تغيّر `APP_SECRET` بعد حفظ حسابات دون خطة لإعادة تشفيرها.