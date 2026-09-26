const qs = (id) => document.getElementById(id);

function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'icon');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `#icon-${name}`);
    svg.appendChild(use);
    return svg;
}

// type: 'idle' | 'recording' | 'busy' | 'success' | 'error'
function setStatus(target, text, type = 'idle') {
    target.className = 'status' + (type !== 'idle' ? ` ${type}` : '');
    target.innerHTML = '';
    if (type === 'recording') target.appendChild(Object.assign(document.createElement('span'), { className: 'rec-dot' }));
    else if (type === 'busy') target.appendChild(Object.assign(document.createElement('span'), { className: 'spinner' }));
    else if (type === 'success') target.appendChild(icon('check'));
    else if (type === 'error') target.appendChild(icon('alert'));
    target.appendChild(Object.assign(document.createElement('span'), { textContent: text }));
}

function showStep(id) {
    document.querySelectorAll('.step').forEach((s) => s.classList.remove('active'));
    qs(id).classList.add('active');
}

// Persisted once per browser so the backend's risk engine can tell "new
// device" apart from "same browser as always" -- the User-Agent string alone
// is identical for every visitor on the same browser/OS build.
function getDeviceId() {
    let id = localStorage.getItem('device_id');
    if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem('device_id', id);
    }
    return id;
}

function authHeaders() {
    return { Authorization: `Bearer ${sessionToken}`, 'X-Device-Id': getDeviceId() };
}

function normalizeWord(w) {
    return w.toLowerCase().replace(/[.,!?;:"'\-]/g, '');
}

function renderPhrase(containerEl, phrase) {
    containerEl.innerHTML = '';
    return phrase.split(/\s+/).map((w) => {
        const span = document.createElement('span');
        span.textContent = w + ' ';
        containerEl.appendChild(span);
        return span;
    });
}

function renderCode(containerEl, code) {
    containerEl.innerHTML = '';
    if (!code) return [];
    return code.split('').map((d) => {
        const span = document.createElement('span');
        span.textContent = d;
        containerEl.appendChild(span);
        return span;
    });
}

// Highlights each expected word as it turns up in the live transcript (best-effort, client-side
// only -- the server checks the actual uploaded audio). Returns whether the whole phrase has been
// heard, and the recognized words that came after it, so a trailing code can be matched next
// without the phrase's own words (e.g. a common "...được không" ending) being mistaken for digits.
function highlightHeardWords(phrase, wordSpans, transcript) {
    const targetWords = phrase.split(/\s+/).map(normalizeWord);
    wordSpans.forEach((s) => s.classList.remove('heard'));
    const recWords = transcript.split(/\s+/).filter(Boolean).map(normalizeWord);
    let ti = 0, matchedThrough = 0;
    for (let ri = 0; ri < recWords.length; ri++) {
        if (ti >= targetWords.length) break;
        if (targetWords[ti] === recWords[ri]) {
            wordSpans[ti].classList.add('heard');
            ti++;
            matchedThrough = ri + 1;
        }
    }
    const done = ti >= targetWords.length;
    return { done, after: done ? recWords.slice(matchedThrough) : [] };
}

// Mirrors backend/services/verify_code.py's digit words, for this live (client-side only) hint.
const DIGIT_WORDS = {
    'không': '0', 'một': '1', 'hai': '2', 'ba': '3', 'bốn': '4',
    'năm': '5', 'sáu': '6', 'bảy': '7', 'bẩy': '7', 'tám': '8', 'chín': '9',
};

function tokenDigits(word) {
    if (/^\d+$/.test(word)) return word.split('');
    return DIGIT_WORDS[word] ? [DIGIT_WORDS[word]] : [];
}

// Same idea as highlightHeardWords, over the recognized words that followed the phrase. A
// recognized word can itself carry more than one digit ("4729"), so it can complete the code
// mid-word. Returns whether every digit has now been heard.
function highlightHeardCode(code, digitSpans, wordsAfterPhrase) {
    digitSpans.forEach((s) => s.classList.remove('heard'));
    let di = 0;
    for (const w of wordsAfterPhrase) {
        if (di >= code.length) break;
        for (const d of tokenDigits(w)) {
            if (di >= code.length) break;
            if (d === code[di]) {
                digitSpans[di].classList.add('heard');
                di++;
            }
        }
    }
    return di >= code.length;
}

async function populateMicSelect(selectEl) {
    // the list is rebuilt after every rejected verify take, so the user's own choice has to be
    // put back or the next take silently records from the default mic
    const chosen = selectEl.value;
    selectEl.innerHTML = '<option value="">Mặc định</option>';
    try {
        const mics = await VoiceRecorder.listMicrophones();
        mics.forEach((m, i) => {
            const opt = document.createElement('option');
            opt.value = m.deviceId;
            opt.textContent = m.label || `Micro ${i + 1}`;
            selectEl.appendChild(opt);
        });
    } catch (e) {
        // mic permission denied or unavailable; keep default option only
    }
    if (chosen && Array.from(selectEl.options || []).some((o) => o.value === chosen)) selectEl.value = chosen;
}

// opens the mic and starts the live level meter as soon as the step is shown,
// and re-opens it whenever the user picks a different device
function wireMicMeter(selectEl, fillEl) {
    const open = () => recorder.openMic(selectEl.value || undefined, (level) => {
        fillEl.style.width = `${level * 100}%`;
    });
    selectEl.onchange = open;
    return open;
}

const MIC_PROBLEM = 'Không mở được micro. Hãy kiểm tra micro đã cắm và bật chưa, hoặc chọn micro khác trong danh sách.';

// Opens the mic for a screen. Resolves to '' when it opened and to MIC_PROBLEM when it did not,
// and never rejects: a mic that won't open must not stop the screen from showing its phrase.
// That used to leave the old phrase on screen after a rejected take while the server had already
// moved on to a new one.
function setUpMic(selectEl, fillEl) {
    return populateMicSelect(selectEl)
        .then(() => wireMicMeter(selectEl, fillEl)())
        .then(() => '', () => MIC_PROBLEM);
}

// The mic can be gone by the time a take starts (unplugged, taken over by another app), and a
// silent take is then the "not heard" the user sees. Try to reopen it once before recording.
async function ensureMicOpen(selectEl, fillEl, statusEl) {
    const live = recorder.stream && recorder.stream.getTracks().some((t) => t.readyState === 'live');
    if (live) return true;
    try {
        await wireMicMeter(selectEl, fillEl)();
        return true;
    } catch (e) {
        setStatus(statusEl, MIC_PROBLEM, 'error');
        return false;
    }
}

const recorder = new VoiceRecorder();
const transcriber = new LiveTranscriber('vi-VN');
let sessionToken = null;
// The password-session token from login. sessionToken is swapped for the post-MFA
// token while re-enrolling, so this is kept to switch back and to log out with.
let loginSessionToken = null;

let enrollPhrases = [];
let enrollIndex = 0;
let enrollWordSpans = [];
let enrollBlob = null;
let enrollTranscript = '';
let enrollRecording = false;

let verifyPhrase = '';
let verifyWordSpans = [];
let verifyBlob = null;
let verifyTranscript = '';
let verifyRecording = false;
let verifyCode = null; // the random number to read after the phrase; null while the server has it switched off
let verifyCodeSpans = [];

// ---------- Auth ----------

async function authenticate(endpoint, isRegister) {
    const email = qs('auth-email').value.trim();
    const password = qs('auth-password').value;
    const statusEl = qs('status-auth');

    const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
    });
    const data = await res.json();

    if (!res.ok) {
        setStatus(statusEl, data.detail || 'Có lỗi xảy ra', 'error');
        return;
    }

    if (isRegister) {
        setStatus(statusEl, 'Đăng ký thành công, hãy đăng nhập.', 'success');
        return;
    }

    sessionToken = data.session_token;
    loginSessionToken = sessionToken;
    qs('app-header').hidden = false;

    const statusRes = await fetch('/api/voice-auth/status', { headers: authHeaders() });
    const status = await statusRes.json();

    if (!status.has_consent) {
        showStep('step-consent');
    } else if (!status.has_voiceprint) {
        await startEnrollUI();
        showStep('step-enroll');
    } else {
        resetVerifyRecoveryPrompt();
        await startVerifyUI();
        showStep('step-verify');
    }
}

qs('btn-login').onclick = () => authenticate('/api/auth/login', false);
qs('btn-register').onclick = () => authenticate('/api/auth/register', true);

// ---------- Consent ----------

qs('btn-accept-consent').onclick = async () => {
    await fetch('/api/voice-auth/consent/accept', { method: 'POST', headers: authHeaders() });
    await startEnrollUI();
    showStep('step-enroll');
};

qs('btn-decline-consent').onclick = () => {
    alert('Bạn cần đồng ý để sử dụng tính năng xác thực giọng nói.');
};

// ---------- Enroll ----------

async function startEnrollUI() {
    // mic setup and the network call are independent, so run them concurrently
    // instead of stacking their latency
    const micSetup = setUpMic(qs('enroll-mic-select'), qs('enroll-mic-meter-fill'));
    const res = await fetch('/api/voice-auth/enroll/start', { method: 'POST', headers: authHeaders() });
    const data = await res.json();
    const micProblem = await micSetup;
    enrollPhrases = data.phrases;
    enrollIndex = 0;
    // how many samples enrollment needs is the server's decision, so the counter
    // and the progress bar are drawn from it rather than hard-coded
    qs('enroll-sample-total').textContent = enrollPhrases.length;
    qs('enroll-progress').replaceChildren(
        ...enrollPhrases.map(() => Object.assign(document.createElement('div'), { className: 'progress-seg' }))
    );
    showEnrollPhrase();
    if (micProblem) setStatus(qs('status'), micProblem, 'error');
}

function showEnrollPhrase() {
    qs('enroll-sample-num').textContent = enrollIndex + 1;
    enrollWordSpans = renderPhrase(qs('enroll-phrase-box'), enrollPhrases[enrollIndex]);
    qs('enroll-preview').style.display = 'none';
    qs('btn-record-sample').style.display = 'flex';
    qs('btn-record-sample').disabled = false;
    qs('btn-record-sample').replaceChildren(icon('mic'), document.createTextNode('Bắt đầu ghi âm'));
    qs('btn-enroll-reshuffle').disabled = false;
    setStatus(qs('status'), 'Nhấn nút để bắt đầu ghi âm.');
    document.querySelectorAll('#step-enroll .progress-seg').forEach((seg, i) => {
        seg.classList.toggle('done', i < enrollIndex);
    });
}

// Asks the server to swap the phrase for this sample (it keeps the phrase it expects per sample,
// so the two must change together). Returns whether it did; on failure the old phrase stays valid.
async function useAnotherEnrollPhrase() {
    const res = await fetch('/api/voice-auth/enroll/reshuffle', { method: 'POST', headers: authHeaders() })
        .catch(() => null);
    if (!res || !res.ok) return false;
    const data = await res.json();
    enrollPhrases[enrollIndex] = data.phrase;
    return true;
}

qs('btn-enroll-reshuffle').onclick = async () => {
    if (await useAnotherEnrollPhrase()) {
        enrollWordSpans = renderPhrase(qs('enroll-phrase-box'), enrollPhrases[enrollIndex]);
    }
};

async function startEnrollRecording() {
    if (!(await ensureMicOpen(qs('enroll-mic-select'), qs('enroll-mic-meter-fill'), qs('status')))) return;
    enrollRecording = true;
    enrollTranscript = '';
    qs('btn-record-sample').replaceChildren(document.createTextNode('Dừng ghi âm'));
    qs('enroll-mic-select').disabled = true;
    qs('btn-enroll-reshuffle').disabled = true;
    setStatus(qs('status'), 'Đang ghi âm — đọc to câu ở trên', 'recording');
    transcriber.onTranscript = (t) => {
        enrollTranscript = t;
        highlightHeardWords(enrollPhrases[enrollIndex], enrollWordSpans, t);
        if (enrollWordSpans.length && enrollWordSpans.every((s) => s.classList.contains('heard'))) {
            stopEnrollRecording();
        }
    };
    recorder.startRecording();
    transcriber.start();
}

async function stopEnrollRecording() {
    if (!enrollRecording) return;
    enrollRecording = false;
    transcriber.stop();
    const { blob } = await recorder.stopRecording();
    enrollBlob = blob;
    qs('btn-record-sample').style.display = 'none';
    qs('enroll-mic-select').disabled = false;
    qs('btn-enroll-reshuffle').disabled = true;
    setStatus(qs('status'), 'Nghe lại bên dưới trước khi xác nhận.');
    const audioEl = qs('enroll-audio-preview');
    audioEl.src = URL.createObjectURL(enrollBlob);
    qs('enroll-preview').style.display = 'block';
}

qs('btn-record-sample').onclick = () => (enrollRecording ? stopEnrollRecording() : startEnrollRecording());

qs('btn-enroll-redo').onclick = () => {
    enrollBlob = null;
    showEnrollPhrase();
};

qs('btn-enroll-confirm').onclick = async () => {
    const confirmBtn = qs('btn-enroll-confirm');
    confirmBtn.disabled = true;
    confirmBtn.replaceChildren(document.createElement('span'));
    confirmBtn.firstChild.className = 'spinner';
    confirmBtn.appendChild(document.createTextNode('Đang xử lý...'));

    // enrollTranscript (from the browser's own Web Speech API, when available)
    // is only used locally to highlight heard words live -- the server no
    // longer trusts a client-supplied transcript, it runs its own
    // speech-to-text on the uploaded audio to check the phrase was read.
    const formData = new FormData();
    formData.append('audio', enrollBlob, 'sample.webm');
    const res = await fetch('/api/voice-auth/enroll/sample', { method: 'POST', headers: authHeaders(), body: formData })
        .catch(() => null);
    const data = res ? await res.json().catch(() => ({})) : {};
    confirmBtn.disabled = false;
    confirmBtn.textContent = 'Xác nhận';

    // Nothing was stored: unreadable audio, too many suspected samples, an enrollment that has
    // expired, no connection. Anything without a status used to fall through to the last branch
    // below and move on to the next phrase as if the sample had been saved, leaving the screen one
    // sample ahead of the server, which then judged every take against the phrase the screen had left.
    if (!res || !res.ok) {
        enrollBlob = null;
        showEnrollPhrase();
        setStatus(qs('status'), data.detail || 'Không gửi được mẫu, hãy ghi âm lại.', 'error');
        return;
    }

    // Both mean "this take wasn't accepted": start it over from a clean screen with a new phrase
    // (the same one if the server can't swap it), and "Đổi câu" usable again. Neither may fall
    // through to the last branch, which advances as if the sample had been stored.
    if (data.status === 'phrase_mismatch' || data.status === 'spoof_detected') {
        const swapped = await useAnotherEnrollPhrase();
        enrollBlob = null;
        showEnrollPhrase();
        setStatus(qs('status'), swapped ? `${data.message}. Đã đổi sang câu mới.` : data.message, 'error');
        return;
    }

    if (data.status === 'enrolled') {
        document.querySelectorAll('#step-enroll .progress-seg').forEach((seg) => seg.classList.add('done'));
        qs('enroll-preview').style.display = 'none';

        if (isReenrolling) {
            // Already fully logged in for this session — no need to verify+OTP again.
            isReenrolling = false;
            // back to the password session: the verify and OTP endpoints don't accept the JWT
            sessionToken = loginSessionToken;
            setStatus(qs('status'), 'Cập nhật giọng nói thành công.', 'success');
            setTimeout(() => showStep('step-success'), 1000);
        } else {
            setStatus(qs('status'), 'Đăng ký thành công. Chuyển sang xác thực...', 'success');
            setTimeout(async () => {
                await startVerifyUI();
                showStep('step-verify');
            }, 1000);
        }
    } else if (data.status === 'enrolling') {
        enrollIndex++;
        showEnrollPhrase();
    } else {
        enrollBlob = null;
        showEnrollPhrase();
        setStatus(qs('status'), 'Máy chủ trả lời không như mong đợi, hãy ghi âm lại.', 'error');
    }
};

// ---------- Verify ----------

// Each digit is its own span, like renderPhrase's words, so it can light up on its own once heard.
function showVerifyCode(code) {
    verifyCode = code || null;
    qs('verify-code-wrap').hidden = !verifyCode;
    verifyCodeSpans = renderCode(qs('verify-code-box'), verifyCode);
    qs('verify-subtitle').textContent = verifyCode
        ? 'Hãy đọc to câu bên dưới, rồi đọc từng chữ số của mã, để xác thực.'
        : 'Hãy đọc to câu bên dưới để xác thực.';
}

function verifyReadyMessage() {
    return verifyCode
        ? 'Nhấn "Bắt đầu nói", đọc to câu ở trên rồi đọc tiếp từng chữ số của mã.'
        : 'Nhấn "Bắt đầu nói" rồi đọc to câu ở trên.';
}

// Puts a phrase (and code) from /verify/prompt on the screen, ready for a new take.
function showVerifyPrompt(data) {
    verifyPhrase = data.phrase;
    verifyWordSpans = renderPhrase(qs('verify-phrase-box'), verifyPhrase);
    showVerifyCode(data.code);
    qs('verify-preview').style.display = 'none';
    qs('btn-start-verify').style.display = 'flex';
    qs('btn-start-verify').disabled = false;
    qs('btn-start-verify').replaceChildren(icon('mic'), document.createTextNode('Bắt đầu nói'));
    qs('btn-stop-verify').disabled = true;
    qs('btn-verify-reshuffle').disabled = false;
    setStatus(qs('status-verify'), verifyReadyMessage());
}

// After a rejected take the server has already thrown the phrase away. If no new one can be
// fetched, the one still on screen can never be accepted, so take it down and leave only the
// button that asks for another.
function showNoVerifyPhrase() {
    verifyPhrase = '';
    verifyWordSpans = [];
    verifyCodeSpans = [];
    qs('verify-phrase-box').textContent = '';
    qs('verify-code-wrap').hidden = true;
    qs('verify-preview').style.display = 'none';
    qs('btn-start-verify').style.display = 'flex';
    qs('btn-start-verify').disabled = true;
    qs('btn-stop-verify').disabled = true;
    qs('btn-verify-reshuffle').disabled = false;
}

// Rejects if there is no new phrase; otherwise resolves to '' or, when the mic would not open,
// to MIC_PROBLEM (the phrase is still shown, so the user can pick another mic and go on).
async function startVerifyUI() {
    // mic setup and the network call are independent, so run them concurrently
    // instead of stacking their latency
    const micSetup = setUpMic(qs('verify-mic-select'), qs('verify-mic-meter-fill'));
    const res = await fetch('/api/voice-auth/verify/prompt', { method: 'POST', headers: authHeaders() });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Không lấy được câu xác thực.');
    }
    const data = await res.json();
    const micProblem = await micSetup;
    showVerifyPrompt(data);
    if (micProblem) setStatus(qs('status-verify'), micProblem, 'error');
    return micProblem;
}

// shown only once a verify attempt has actually failed; a fresh login starts clean
let recoveryPurpose = 'reenroll'; // 'reenroll' (voice not cooperating) | 'unlock' (fraud lock)

function resetVerifyRecoveryPrompt() {
    qs('verify-recovery').hidden = true;
    recoveryPurpose = 'reenroll';
    qs('verify-recovery-hint').textContent = 'Giọng nói không nhận diện được sau nhiều lần thử?';
    qs('btn-verify-recovery').textContent = 'Bạn có muốn đăng ký lại giọng nói không?';
}

qs('btn-verify-reshuffle').onclick = async () => {
    const res = await fetch('/api/voice-auth/verify/prompt', { method: 'POST', headers: authHeaders() })
        .catch(() => null);
    if (!res || !res.ok) {
        setStatus(qs('status-verify'), 'Không lấy được câu mới, hãy thử lại sau ít giây.', 'error');
        return;
    }
    showVerifyPrompt(await res.json());
};

async function startVerifyRecording() {
    if (!(await ensureMicOpen(qs('verify-mic-select'), qs('verify-mic-meter-fill'), qs('status-verify')))) return;
    verifyRecording = true;
    verifyTranscript = '';
    transcriber.onTranscript = (t) => {
        verifyTranscript = t;
        const { done, after } = highlightHeardWords(verifyPhrase, verifyWordSpans, t);
        if (verifyCode) {
            // the code only starts after the whole phrase has been heard, so its own words
            // (a phrase can end in a digit word, e.g. "...được không") are never mistaken for digits
            if (done && highlightHeardCode(verifyCode, verifyCodeSpans, after)) {
                finishVerifyRecording();
            }
        } else if (done && verifyWordSpans.length) {
            stopVerifyRecording();
        }
    };
    recorder.startRecording();
    transcriber.start();
    qs('btn-start-verify').disabled = true;
    qs('btn-stop-verify').disabled = false;
    qs('verify-mic-select').disabled = true;
    qs('btn-verify-reshuffle').disabled = true;
    setStatus(qs('status-verify'), verifyCode
        ? 'Đang ghi âm — đọc câu, rồi đọc từng chữ số của mã. Đọc xong sẽ tự động gửi.'
        : 'Đang ghi âm — đọc to câu ở trên', 'recording');
}

// Common to both ways a recording ends: stop listening, grab the audio, and leave the controls
// in the "not recording" state. What happens next (show a preview, or send right away) differs.
async function stopRecordingAndKeepBlob() {
    verifyRecording = false;
    transcriber.stop();
    const { blob } = await recorder.stopRecording();
    verifyBlob = blob;
    qs('btn-start-verify').style.display = 'none';
    qs('btn-stop-verify').disabled = true;
    qs('verify-mic-select').disabled = false;
    qs('btn-verify-reshuffle').disabled = true;
}

async function stopVerifyRecording() {
    if (!verifyRecording) return;
    await stopRecordingAndKeepBlob();
    setStatus(qs('status-verify'), 'Nghe lại bên dưới trước khi gửi.');
    const audioEl = qs('verify-audio-preview');
    audioEl.src = URL.createObjectURL(verifyBlob);
    qs('verify-preview').style.display = 'block';
}

// Reading the code out is itself the "done" signal -- there is nothing left to check before
// sending, so this skips the manual preview/confirm step stopVerifyRecording leads to.
async function finishVerifyRecording() {
    if (!verifyRecording) return;
    await stopRecordingAndKeepBlob();
    setStatus(qs('status-verify'), 'Đã đọc xong, đang gửi xác thực...', 'busy');
    await submitVerify();
}

qs('btn-start-verify').onclick = startVerifyRecording;
qs('btn-stop-verify').onclick = stopVerifyRecording;

qs('btn-verify-redo').onclick = () => {
    verifyBlob = null;
    qs('btn-start-verify').style.display = 'flex';
    qs('btn-start-verify').disabled = false;
    qs('btn-verify-reshuffle').disabled = false;
    qs('verify-preview').style.display = 'none';
    verifyWordSpans.forEach((s) => s.classList.remove('heard'));
    verifyCodeSpans.forEach((s) => s.classList.remove('heard'));
    setStatus(qs('status-verify'), verifyReadyMessage());
};

async function submitVerify() {
    const confirmBtn = qs('btn-verify-confirm');
    confirmBtn.disabled = true;
    confirmBtn.replaceChildren(document.createElement('span'));
    confirmBtn.firstChild.className = 'spinner';
    confirmBtn.appendChild(document.createTextNode('Đang xác thực...'));

    // verifyTranscript is only used locally to highlight heard words live --
    // the server runs its own speech-to-text on the uploaded audio instead
    // of trusting a client-supplied transcript.
    const formData = new FormData();
    formData.append('audio', verifyBlob, 'voice.webm');
    const res = await fetch('/api/voice-auth/verify', { method: 'POST', headers: authHeaders(), body: formData })
        .catch(() => null);
    // an HTML error page from nginx or no answer at all is a failed attempt like any other,
    // not an exception that leaves this screen half-way through a take
    const data = res ? await res.json().catch(() => ({})) : {};
    confirmBtn.disabled = false;
    confirmBtn.textContent = 'Gửi xác thực';

    if (data.decision === 'mfa_required') {
        // show the OTP screen right away instead of waiting on the email
        // round-trip; sendOtp reports failure onto that screen if it happens
        otpMode = 'mfa';
        clearOtpBoxes();
        showStep('step-otp');
        startOtpCountdown();
        sendOtp('/api/voice-auth/otp/send');
    } else {
        verifyBlob = null;
        // The server used up the phrase when it judged this take, and with no answer at all we
        // can't tell whether it did, so a new one is fetched straight away either way.
        let freshPhrase = true;
        let micProblem = '';
        try {
            micProblem = await startVerifyUI();
        } catch (err) {
            freshPhrase = false;
            showNoVerifyPhrase();
        }
        setStatus(
            qs('status-verify'),
            verifyFailureMessage(data, freshPhrase)
                + (freshPhrase ? '' : ' Chưa lấy được câu mới, hãy nhấn nút "Đổi câu khác" cạnh câu.')
                + (micProblem ? ` ${micProblem}` : ''),
            'error'
        );
        recoveryPurpose = data.reason === 'fraud_lockout' ? 'unlock' : 'reenroll';
        qs('verify-recovery-hint').textContent = recoveryPurpose === 'unlock'
            ? 'Tài khoản đã bị khoá do nghi ngờ tấn công.'
            : 'Giọng nói không nhận diện được sau nhiều lần thử?';
        qs('btn-verify-recovery').textContent = recoveryPurpose === 'unlock'
            ? 'Xác thực qua email để mở khoá'
            : 'Bạn có muốn đăng ký lại giọng nói không?';
        qs('verify-recovery').hidden = false;
    }
}

qs('btn-verify-confirm').onclick = submitVerify;

// newPhrase: whether a new phrase (and code) is on screen. Every rejected take gets one, so the
// message says so, unless fetching it failed.
function verifyFailureMessage(data, newPhrase = true) {
    const again = newPhrase ? 'Đây là câu mới, hãy thử lại.' : 'Hãy thử lại.';
    switch (data.reason) {
        case 'audio_too_quiet':
            return `Giọng bạn quá nhỏ hoặc micro không thu được tiếng. Hãy kiểm tra micro, nói to và rõ hơn. ${again}`;
        case 'phrase_mismatch':
            return verifyCode
                ? `Câu đọc hoặc mã không khớp. ${newPhrase ? 'Đây là câu và mã mới, hãy thử lại.' : 'Hãy thử lại.'}`
                : `Câu đọc không khớp. ${again}`;
        case 'voiceprint_mismatch':
            return `Giọng nói không khớp với hồ sơ đã đăng ký. ${again}`;
        case 'suspicious_context':
            return `Phát hiện dấu hiệu bất thường (thiết bị hoặc vị trí lạ). ${newPhrase ? 'Đây là câu mới, hãy thử lại' : 'Hãy thử lại'} để xác minh thêm.`;
        case 'spoofing_detected':
            return `Bạn có đang dùng bản ghi âm hoặc giọng giả không? Nếu bạn cố tình dùng cách này để vượt qua xác thực, hệ thống sẽ xử lý nghiêm túc. Còn ${data.tries_left} lần thử.`;
        case 'fraud_lockout':
            return 'Phát hiện dấu hiệu tấn công giả mạo giọng nói nghiêm trọng. Tài khoản đã bị khoá xác thực giọng nói cho đến khi bạn xác thực lại qua email.';
        case 'rate_limit_exceeded':
            return 'Bạn đã thử quá nhiều lần. Vui lòng đợi ít phút rồi thử lại.';
        case 'voiceprint_outdated':
            return 'Hồ sơ giọng nói của bạn được tạo bằng một phiên bản cũ của hệ thống nên không thể dùng nữa. Hãy đăng ký lại giọng nói bằng nút bên dưới.';
        default:
            return `Xác thực thất bại. ${again}`;
    }
}

qs('btn-verify-recovery').onclick = () => {
    otpMode = 'recovery';
    clearOtpBoxes();
    showStep('step-otp');
    startOtpCountdown();
    sendOtp('/api/voice-auth/recovery/otp/send');
};

// fires an OTP send without blocking the screen transition on the email
// round-trip; any failure is reported onto the (already-visible) OTP screen
async function sendOtp(endpoint) {
    const res = await fetch(endpoint, { method: 'POST', headers: authHeaders() });
    if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setStatus(qs('status-otp'), data.detail || 'Không gửi được mã, thử lại sau.', 'error');
    }
}

// ---------- OTP ----------

const OTP_TTL_SECONDS = 300; // must match backend OTP_TTL_SECONDS in services/otp.py
let otpInterval = null;
let otpMode = 'mfa'; // 'mfa' (after a successful voice verify) | 'recovery' (voice not cooperating)

function startOtpCountdown() {
    clearInterval(otpInterval);
    let remaining = OTP_TTL_SECONDS;
    const timerEl = qs('otp-timer');

    const render = () => {
        const m = Math.floor(remaining / 60);
        const s = String(remaining % 60).padStart(2, '0');
        timerEl.textContent = `${m}:${s}`;
    };
    render();

    otpInterval = setInterval(() => {
        remaining--;
        if (remaining <= 0) {
            clearInterval(otpInterval);
            timerEl.textContent = '0:00';
            setStatus(qs('status-otp'), 'Mã đã hết hạn, nhấn "Gửi lại mã".', 'error');
            return;
        }
        render();
    }, 1000);
}

// ---------- OTP boxes: 6 single-digit inputs, auto-advance, auto-submit ----------

function getOtpBoxes() {
    return Array.from(document.querySelectorAll('.otp-box'));
}

function clearOtpBoxes() {
    const boxes = getOtpBoxes();
    boxes.forEach((b) => (b.value = ''));
    if (boxes[0]) boxes[0].focus();
}

let otpSubmitting = false;

async function submitOtp() {
    const boxes = getOtpBoxes();
    const code = boxes.map((b) => b.value).join('');
    if (code.length !== 6 || otpSubmitting) return;
    otpSubmitting = true;

    const endpoint = otpMode === 'recovery' ? '/api/voice-auth/recovery/otp/verify' : '/api/voice-auth/otp/verify';
    const res = await fetch(endpoint, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
    });
    otpSubmitting = false;

    if (res.ok) {
        const data = await res.json();
        localStorage.setItem('access_token', data.access_token);
        clearInterval(otpInterval);
        clearOtpBoxes();
        resetVerifyRecoveryPrompt();
        recorder.closeMic();

        if (otpMode === 'recovery') {
            qs('success-subtitle').textContent = recoveryPurpose === 'unlock'
                ? 'Xác thực qua email đã hoàn tất. Tài khoản đã được mở khoá, bạn có thể xác thực lại giọng nói bên dưới.'
                : 'Xác thực qua email đã hoàn tất. Bạn có thể đăng ký lại giọng nói bên dưới.';
        } else {
            qs('success-subtitle').textContent = 'Xác thực bằng giọng nói và OTP đã hoàn tất.';
        }
        showStep('step-success');
    } else {
        setStatus(qs('status-otp'), 'Mã OTP không hợp lệ hoặc đã hết hạn.', 'error');
        clearOtpBoxes();
    }
}

function wireOtpBoxes() {
    const boxes = getOtpBoxes();
    boxes.forEach((box, i) => {
        box.addEventListener('input', () => {
            box.value = box.value.replace(/\D/g, '').slice(-1);
            if (box.value && i < boxes.length - 1) boxes[i + 1].focus();
            if (boxes.every((b) => b.value)) submitOtp();
        });
        box.addEventListener('keydown', (e) => {
            if (e.key === 'Backspace' && !box.value && i > 0) boxes[i - 1].focus();
        });
        box.addEventListener('paste', (e) => {
            e.preventDefault();
            const text = (e.clipboardData || window.clipboardData).getData('text').replace(/\D/g, '').slice(0, 6);
            text.split('').forEach((ch, idx) => { if (boxes[idx]) boxes[idx].value = ch; });
            const nextEmpty = boxes.findIndex((b) => !b.value);
            (boxes[nextEmpty] || boxes[boxes.length - 1]).focus();
            if (boxes.every((b) => b.value)) submitOtp();
        });
    });
}

wireOtpBoxes();

qs('btn-resend-otp').onclick = async () => {
    const btn = qs('btn-resend-otp');
    btn.disabled = true;
    const endpoint = otpMode === 'recovery' ? '/api/voice-auth/recovery/otp/send' : '/api/voice-auth/otp/send';
    const res = await fetch(endpoint, { method: 'POST', headers: authHeaders() });
    btn.disabled = false;

    if (res.ok) {
        startOtpCountdown();
        clearOtpBoxes();
        setStatus(qs('status-otp'), 'Đã gửi lại mã mới.', 'success');
    } else {
        const data = await res.json().catch(() => ({}));
        setStatus(qs('status-otp'), data.detail || 'Không gửi lại được, thử lại sau.', 'error');
    }
};

// ---------- Re-verify ----------

qs('btn-reverify').onclick = async () => {
    // sessionToken is still the password-session token from login (valid
    // ~30 min); OTP is required again to finish, same as any voice verify.
    resetVerifyRecoveryPrompt();
    try {
        await startVerifyUI();
        showStep('step-verify');
    } catch (e) {
        alert('Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.');
        qs('btn-logout-header').click();
    }
};

// ---------- Re-enroll ----------

let isReenrolling = false;

qs('btn-reenroll').onclick = async () => {
    const accessToken = localStorage.getItem('access_token');
    if (!accessToken) return;
    // Enroll endpoints require proof of a completed voice+OTP verify once a
    // voiceprint already exists, so swap in the post-MFA token here.
    sessionToken = accessToken;
    isReenrolling = true;
    await startEnrollUI();
    showStep('step-enroll');
};

// ---------- Logout ----------

qs('btn-logout-header').onclick = async () => {
    // best-effort: invalidate the server-side session and revoke the post-MFA JWT
    // so a copied/leaked token can't keep being used after the user has explicitly
    // logged out. Both are sent because the session may have expired while the JWT
    // (1 h against 30 min) is still alive, and the server finds the user from either.
    const accessToken = localStorage.getItem('access_token');
    const logoutToken = loginSessionToken || sessionToken;
    if (logoutToken || accessToken) {
        const headers = { 'X-Device-Id': getDeviceId() };
        if (logoutToken) headers.Authorization = `Bearer ${logoutToken}`;
        if (accessToken) headers['X-Access-Token'] = accessToken;
        fetch('/api/auth/logout', { method: 'POST', headers }).catch(() => {});
    }
    localStorage.removeItem('access_token');
    sessionToken = null;
    loginSessionToken = null;
    isReenrolling = false;
    clearInterval(otpInterval);
    recorder.closeMic();
    qs('auth-email').value = '';
    qs('auth-password').value = '';
    setStatus(qs('status-auth'), '', 'idle');
    qs('app-header').hidden = true;
    showStep('step-auth');
};
