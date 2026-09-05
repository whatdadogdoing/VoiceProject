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

function authHeaders() {
    return { Authorization: `Bearer ${sessionToken}` };
}

function normalizeWord(w) {
    return w.toLowerCase().replace(/[.,!?;:"']/g, '');
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

function highlightHeardWords(phrase, wordSpans, transcript) {
    const targetWords = phrase.split(/\s+/);
    wordSpans.forEach((s) => s.classList.remove('heard'));
    const recWords = transcript.split(/\s+/).filter(Boolean).map(normalizeWord);
    let ti = 0;
    for (const rw of recWords) {
        if (ti >= targetWords.length) break;
        if (normalizeWord(targetWords[ti]) === rw) {
            wordSpans[ti].classList.add('heard');
            ti++;
        }
    }
}

async function populateMicSelect(selectEl) {
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

const recorder = new VoiceRecorder();
const transcriber = new LiveTranscriber('vi-VN');
let sessionToken = null;

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
    const micSelect = qs('enroll-mic-select');
    const micSetup = populateMicSelect(micSelect).then(() => wireMicMeter(micSelect, qs('enroll-mic-meter-fill'))());
    const res = await fetch('/api/voice-auth/enroll/start', { method: 'POST', headers: authHeaders() });
    const data = await res.json();
    await micSetup;
    enrollPhrases = data.phrases;
    enrollIndex = 0;
    showEnrollPhrase();
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

qs('btn-enroll-reshuffle').onclick = async () => {
    const res = await fetch('/api/voice-auth/enroll/reshuffle', { method: 'POST', headers: authHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    enrollPhrases[enrollIndex] = data.phrase;
    enrollWordSpans = renderPhrase(qs('enroll-phrase-box'), data.phrase);
};

async function startEnrollRecording() {
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

    const formData = new FormData();
    formData.append('audio', enrollBlob, 'sample.webm');
    formData.append('transcript', enrollTranscript);
    const res = await fetch('/api/voice-auth/enroll/sample', { method: 'POST', headers: authHeaders(), body: formData });
    const data = await res.json();
    confirmBtn.disabled = false;
    confirmBtn.textContent = 'Xác nhận';

    if (data.status === 'phrase_mismatch') {
        qs('enroll-preview').style.display = 'none';
        qs('btn-record-sample').style.display = 'flex';
        setStatus(qs('status'), data.message, 'error');
        return;
    }

    if (data.status === 'enrolled') {
        document.querySelectorAll('#step-enroll .progress-seg').forEach((seg) => seg.classList.add('done'));
        qs('enroll-preview').style.display = 'none';

        if (isReenrolling) {
            // Already fully logged in for this session — no need to verify+OTP again.
            isReenrolling = false;
            setStatus(qs('status'), 'Cập nhật giọng nói thành công.', 'success');
            setTimeout(() => showStep('step-success'), 1000);
        } else {
            setStatus(qs('status'), 'Đăng ký thành công. Chuyển sang xác thực...', 'success');
            setTimeout(async () => {
                await startVerifyUI();
                showStep('step-verify');
            }, 1000);
        }
    } else {
        enrollIndex++;
        showEnrollPhrase();
    }
};

// ---------- Verify ----------

async function startVerifyUI() {
    // mic setup and the network call are independent, so run them concurrently
    // instead of stacking their latency
    const micSelect = qs('verify-mic-select');
    const micSetup = populateMicSelect(micSelect).then(() => wireMicMeter(micSelect, qs('verify-mic-meter-fill'))());
    const res = await fetch('/api/voice-auth/verify/prompt', { method: 'POST', headers: authHeaders() });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Không lấy được câu xác thực.');
    }
    const data = await res.json();
    await micSetup;
    verifyPhrase = data.phrase;
    verifyWordSpans = renderPhrase(qs('verify-phrase-box'), verifyPhrase);
    qs('verify-preview').style.display = 'none';
    qs('btn-start-verify').style.display = 'flex';
    qs('btn-start-verify').disabled = false;
    qs('btn-start-verify').replaceChildren(icon('mic'), document.createTextNode('Bắt đầu nói'));
    qs('btn-stop-verify').disabled = true;
    qs('btn-verify-reshuffle').disabled = false;
    setStatus(qs('status-verify'), 'Nhấn "Bắt đầu nói" rồi đọc to câu ở trên.');
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
    const res = await fetch('/api/voice-auth/verify/prompt', { method: 'POST', headers: authHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    verifyPhrase = data.phrase;
    verifyWordSpans = renderPhrase(qs('verify-phrase-box'), verifyPhrase);
};

async function startVerifyRecording() {
    verifyRecording = true;
    verifyTranscript = '';
    transcriber.onTranscript = (t) => {
        verifyTranscript = t;
        highlightHeardWords(verifyPhrase, verifyWordSpans, t);
        if (verifyWordSpans.length && verifyWordSpans.every((s) => s.classList.contains('heard'))) {
            stopVerifyRecording();
        }
    };
    recorder.startRecording();
    transcriber.start();
    qs('btn-start-verify').disabled = true;
    qs('btn-stop-verify').disabled = false;
    qs('verify-mic-select').disabled = true;
    qs('btn-verify-reshuffle').disabled = true;
    setStatus(qs('status-verify'), 'Đang ghi âm — đọc to câu ở trên', 'recording');
}

async function stopVerifyRecording() {
    if (!verifyRecording) return;
    verifyRecording = false;
    transcriber.stop();
    const { blob } = await recorder.stopRecording();
    verifyBlob = blob;
    qs('btn-start-verify').style.display = 'none';
    qs('btn-stop-verify').disabled = true;
    qs('verify-mic-select').disabled = false;
    qs('btn-verify-reshuffle').disabled = true;
    setStatus(qs('status-verify'), 'Nghe lại bên dưới trước khi gửi.');
    const audioEl = qs('verify-audio-preview');
    audioEl.src = URL.createObjectURL(verifyBlob);
    qs('verify-preview').style.display = 'block';
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
    setStatus(qs('status-verify'), 'Nhấn "Bắt đầu nói" rồi đọc to câu ở trên.');
};

qs('btn-verify-confirm').onclick = async () => {
    const confirmBtn = qs('btn-verify-confirm');
    confirmBtn.disabled = true;
    confirmBtn.replaceChildren(document.createElement('span'));
    confirmBtn.firstChild.className = 'spinner';
    confirmBtn.appendChild(document.createTextNode('Đang xác thực...'));

    const formData = new FormData();
    formData.append('audio', verifyBlob, 'voice.webm');
    formData.append('transcript', verifyTranscript);
    const res = await fetch('/api/voice-auth/verify', { method: 'POST', headers: authHeaders(), body: formData });
    const data = await res.json();
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
        await startVerifyUI();
        setStatus(qs('status-verify'), verifyFailureMessage(data), 'error');
        recoveryPurpose = data.reason === 'fraud_lockout' ? 'unlock' : 'reenroll';
        qs('verify-recovery-hint').textContent = recoveryPurpose === 'unlock'
            ? 'Tài khoản đã bị khoá do nghi ngờ tấn công.'
            : 'Giọng nói không nhận diện được sau nhiều lần thử?';
        qs('btn-verify-recovery').textContent = recoveryPurpose === 'unlock'
            ? 'Xác thực qua email để mở khoá'
            : 'Bạn có muốn đăng ký lại giọng nói không?';
        qs('verify-recovery').hidden = false;
    }
};

function verifyFailureMessage(data) {
    switch (data.reason) {
        case 'audio_too_quiet':
            return 'Giọng bạn quá nhỏ, không thể xác định rõ. Hãy nói to và rõ hơn, rồi thử lại.';
        case 'phrase_mismatch':
            return 'Câu đọc không khớp. Đây là câu mới, hãy thử lại.';
        case 'voiceprint_mismatch':
            return 'Giọng nói không khớp với hồ sơ đã đăng ký. Đây là câu mới, hãy thử lại.';
        case 'suspicious_context':
            return 'Phát hiện dấu hiệu bất thường (thiết bị hoặc vị trí lạ). Hãy thử lại để xác minh thêm.';
        case 'spoofing_detected':
            return `Bạn có đang dùng bản ghi âm hoặc giọng giả không? Nếu bạn cố tình dùng cách này để vượt qua xác thực, hệ thống sẽ xử lý nghiêm túc. Còn ${data.tries_left} lần thử.`;
        case 'fraud_lockout':
            return 'Phát hiện dấu hiệu tấn công giả mạo giọng nói nghiêm trọng. Tài khoản đã bị khoá xác thực giọng nói cho đến khi bạn xác thực lại qua email.';
        case 'rate_limit_exceeded':
            return 'Bạn đã thử quá nhiều lần. Vui lòng đợi ít phút rồi thử lại.';
        default:
            return 'Xác thực thất bại. Đây là câu mới, hãy thử lại.';
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
    const res = await fetch(`${endpoint}?code=${encodeURIComponent(code)}`, {
        method: 'POST',
        headers: authHeaders()
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

qs('btn-logout-header').onclick = () => {
    localStorage.removeItem('access_token');
    sessionToken = null;
    isReenrolling = false;
    clearInterval(otpInterval);
    recorder.closeMic();
    qs('auth-email').value = '';
    qs('auth-password').value = '';
    setStatus(qs('status-auth'), '', 'idle');
    qs('app-header').hidden = true;
    showStep('step-auth');
};
