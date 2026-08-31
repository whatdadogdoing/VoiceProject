const qs = (id) => document.getElementById(id);

function setStatus(el, text, type = '') {
    el.textContent = text;
    el.className = 'status' + (type ? ` ${type}` : '');
}

// ---------- Reference recording ----------

let mediaRecorder = null;
let audioChunks = [];
let recording = false;

async function checkReferenceStatus() {
    const res = await fetch('/api/reference/status');
    const data = await res.json();
    if (data.has_reference) {
        setStatus(qs('status-reference'), 'Đã có mẫu giọng nói đã lưu.', 'success');
    }
}

qs('btn-record').onclick = async () => {
    if (!recording) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
        audioChunks = [];
        mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
        mediaRecorder.start();
        recording = true;
        qs('btn-record').textContent = 'Dừng ghi âm';
        setStatus(qs('status-reference'), 'Đang ghi âm...', 'recording');
    } else {
        mediaRecorder.onstop = async () => {
            mediaRecorder.stream.getTracks().forEach((t) => t.stop());
            const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType });
            qs('reference-preview').src = URL.createObjectURL(blob);
            qs('reference-preview').style.display = 'block';

            setStatus(qs('status-reference'), 'Đang lưu...', '');
            const formData = new FormData();
            formData.append('audio', blob, 'reference.webm');
            const res = await fetch('/api/reference', { method: 'POST', body: formData });
            if (res.ok) {
                setStatus(qs('status-reference'), 'Đã lưu mẫu giọng nói.', 'success');
            } else {
                setStatus(qs('status-reference'), 'Lưu thất bại, thử lại.', 'error');
            }
        };
        mediaRecorder.stop();
        recording = false;
        qs('btn-record').textContent = 'Bắt đầu ghi âm';
    }
};

// ---------- Synthesize ----------

qs('btn-synthesize').onclick = async () => {
    const text = qs('fake-text').value.trim();
    if (!text) return;

    const btn = qs('btn-synthesize');
    btn.disabled = true;
    setStatus(qs('status-synthesize'), 'Đang tạo giọng giả (có thể mất một lúc)...', '');

    const formData = new FormData();
    formData.append('text', text);
    const res = await fetch('/api/synthesize', { method: 'POST', body: formData });
    btn.disabled = false;

    if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setStatus(qs('status-synthesize'), data.detail || 'Tạo giọng giả thất bại.', 'error');
        return;
    }

    const blob = await res.blob();
    qs('fake-preview').src = URL.createObjectURL(blob);
    qs('fake-preview').style.display = 'block';
    setStatus(qs('status-synthesize'), 'Đã tạo xong, nhấn nút bên dưới để nghe thử.', 'success');
};

// ---------- Attack ----------

async function checkAttackStatus() {
    const res = await fetch('/api/attack/status');
    const data = await res.json();
    if (data.logged_in) showAttackReady(data.email);
}

function showAttackReady(email) {
    qs('attack-login').style.display = 'none';
    qs('attack-ready').style.display = 'block';
    qs('attack-email-display').textContent = email;
}

qs('btn-attack-login').onclick = async () => {
    const email = qs('attack-email').value.trim();
    const password = qs('attack-password').value;
    if (!email || !password) return;

    const btn = qs('btn-attack-login');
    btn.disabled = true;
    const formData = new FormData();
    formData.append('email', email);
    formData.append('password', password);
    const res = await fetch('/api/attack/login', { method: 'POST', body: formData });
    btn.disabled = false;

    if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setStatus(qs('status-attack'), data.detail || 'Đăng nhập thất bại.', 'error');
        return;
    }

    qs('attack-password').value = '';
    setStatus(qs('status-attack'), '', '');
    showAttackReady(email);
};

qs('btn-attack-send').onclick = async () => {
    const btn = qs('btn-attack-send');
    btn.disabled = true;
    setStatus(qs('status-attack'), 'Đang lấy câu thử thách và tạo giọng giả...', '');
    qs('attack-result').style.display = 'none';

    const res = await fetch('/api/attack/send', { method: 'POST' });
    btn.disabled = false;

    if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setStatus(qs('status-attack'), data.detail || 'Gửi thất bại.', 'error');
        return;
    }

    const data = await res.json();
    setStatus(qs('status-attack'), '', '');

    const decision = data.result.decision;
    const isAccepted = decision === 'mfa_required';
    qs('attack-result').innerHTML = `
        <p><strong>Câu thử thách:</strong> ${data.phrase}</p>
        <p><strong>Kết quả:</strong> <span class="${isAccepted ? 'decision-accept' : 'decision-reject'}">${decision}</span></p>
        ${data.result.reason ? `<p><strong>Lý do:</strong> ${data.result.reason}</p>` : ''}
    `;
    qs('attack-result').style.display = 'block';
};

checkReferenceStatus();
checkAttackStatus();
