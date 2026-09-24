class VoiceRecorder {
    static _micPermissionGranted = false;

    constructor() {
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.stream = null;
        this.currentDeviceId = undefined;
        this.audioCtx = null;
        this.analyser = null;
        this.levelRAF = null;
    }

    static async listMicrophones() {
        // labels are only populated once mic permission has been granted; that grant
        // is sticky for the origin, so only probe for it the first time, not on
        // every call (each getUserMedia round-trip is real, avoidable latency).
        if (!VoiceRecorder._micPermissionGranted) {
            const s = await navigator.mediaDevices.getUserMedia({ audio: true });
            s.getTracks().forEach(t => t.stop());
            VoiceRecorder._micPermissionGranted = true;
        }
        const devices = await navigator.mediaDevices.enumerateDevices();
        return devices.filter(d => d.kind === 'audioinput');
    }

    // opens the mic and starts the live level meter, independent of recording,
    // so the UI can show mic activity before the user commits to a take
    async openMic(deviceId, onLevel) {
        this.closeMic();

        const audioConstraints = {
            sampleRate: 16000,
            channelCount: 1,
            // Kept off. Measured A/B on 10 recordings each way: with the browser's
            // own noise suppression / AGC / echo cancellation ON, speech-to-text
            // could place only 4 of 10 clips against a challenge phrase (9 of 10
            // with it off), 3 of 10 were flagged as spoofed by AASIST-L (0 of 10
            // off), and the speaker match dropped. AASIST-L was trained on
            // unprocessed ASVspoof2019 audio, so processed speech reads as
            // vocoder-like and causes false "spoofing_detected" rejections.
            echoCancellation: false,
            noiseSuppression: false,
            autoGainControl: false
        };
        if (deviceId) audioConstraints.deviceId = { exact: deviceId };

        this.stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
        this.currentDeviceId = deviceId;
        if (onLevel) this._startLevelMeter(onLevel);
    }

    closeMic() {
        this._stopLevelMeter();
        if (this.stream) {
            this.stream.getTracks().forEach(t => t.stop());
            this.stream = null;
        }
        this.currentDeviceId = undefined;
    }

    _startLevelMeter(onLevel) {
        this.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const source = this.audioCtx.createMediaStreamSource(this.stream);
        this.analyser = this.audioCtx.createAnalyser();
        this.analyser.fftSize = 512;
        source.connect(this.analyser);

        const data = new Uint8Array(this.analyser.frequencyBinCount);
        const tick = () => {
            this.analyser.getByteTimeDomainData(data);
            let sumSquares = 0;
            for (let i = 0; i < data.length; i++) {
                const v = (data[i] - 128) / 128;
                sumSquares += v * v;
            }
            const rms = Math.sqrt(sumSquares / data.length);
            onLevel(Math.min(1, rms * 4));
            this.levelRAF = requestAnimationFrame(tick);
        };
        tick();
    }

    _stopLevelMeter() {
        if (this.levelRAF) cancelAnimationFrame(this.levelRAF);
        this.levelRAF = null;
        if (this.audioCtx) this.audioCtx.close();
        this.audioCtx = null;
        this.analyser = null;
    }

    // requires openMic() to have been called first; records on the already-open stream
    startRecording() {
        this.audioChunks = [];
        this.mediaRecorder = new MediaRecorder(this.stream, {
            mimeType: 'audio/webm;codecs=opus'
        });
        this.mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) this.audioChunks.push(e.data);
        };
        // no timeslice: fires a single dataavailable with the complete file on
        // stop(), avoiding chunk-concatenation producing an unplayable WebM
        this.mediaRecorder.start();
        this.startedAt = Date.now();
    }

    stopRecording() {
        return new Promise((resolve) => {
            this.mediaRecorder.onstop = () => {
                const blob = new Blob(this.audioChunks, { type: this.mediaRecorder.mimeType });
                const durationSec = (Date.now() - this.startedAt) / 1000;
                this.audioChunks = [];
                // mic stays open (meter keeps running) for the next take; closeMic() releases it
                resolve({ blob, durationSec });
            };
            this.mediaRecorder.stop();
        });
    }
}

class LiveTranscriber {
    constructor(lang = 'vi-VN') {
        const SpeechRecognitionImpl = window.SpeechRecognition || window.webkitSpeechRecognition;
        this.supported = !!SpeechRecognitionImpl;
        if (!this.supported) return;

        this.recognition = new SpeechRecognitionImpl();
        this.recognition.lang = lang;
        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        this.onTranscript = null;

        this.recognition.onresult = (event) => {
            let transcript = '';
            for (let i = 0; i < event.results.length; i++) {
                transcript += event.results[i][0].transcript + ' ';
            }
            if (this.onTranscript) this.onTranscript(transcript.trim());
        };

        this.recognition.onerror = () => {};
    }

    start() {
        if (!this.supported) return;
        try { this.recognition.start(); } catch (e) {}
    }

    stop() {
        if (!this.supported) return;
        try { this.recognition.stop(); } catch (e) {}
    }
}
