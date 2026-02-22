class PCM16Processor extends AudioWorkletProcessor {
    process(inputs, outputs, parameters) {
        const input = inputs[0];
        if (input && input.length > 0) {
            const float32Data = input[0];
            const pcm16 = new Int16Array(float32Data.length);
            for (let i = 0; i < float32Data.length; i++) {
                pcm16[i] = Math.max(-32768, Math.min(32767, Math.floor(float32Data[i] * 32768)));
            }
            this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
        }
        return true;
    }
}

registerProcessor('pcm16-processor', PCM16Processor);
