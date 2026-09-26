void kernel_gelu_s8(const int8_t *input, int8_t *output, int n, float scale_in, float scale_out, int activation_min, int activation_max)
{
    const float inv_scale_out = 1.0f / scale_out;
    const float sqrt_half = 0.7071067811865475244f;
    
    if (n <= 256) {
        int i = 0;
        for (; i + 4 <= n; i += 4) {
            float f0 = input[i+0] * scale_in;
            float f1 = input[i+1] * scale_in;
            float f2 = input[i+2] * scale_in;
            float f3 = input[i+3] * scale_in;
            
            float scaled_f0 = f0 * sqrt_half;
            float scaled_f1 = f1 * sqrt_half;
            float scaled_f2 = f2 * sqrt_half;
            float scaled_f3 = f3 * sqrt_half;
            
            float erf_val0 = erff(scaled_f0);
            float erf_val1 = erff(scaled_f1);
            float erf_val2 = erff(scaled_f2);
            float erf_val3 = erff(scaled_f3);
            
            float y0 = 0.5f * f0 * (1.0f + erf_val0);
            float y1 = 0.5f * f1 * (1.0f + erf_val1);
            float y2 = 0.5f * f2 * (1.0f + erf_val2);
            float y3 = 0.5f * f3 * (1.0f + erf_val3);
            
            int32_t v0 = (int32_t)roundf(y0 * inv_scale_out);
            int32_t v1 = (int32_t)roundf(y1 * inv_scale_out);
            int32_t v2 = (int32_t)roundf(y2 * inv_scale_out);
            int32_t v3 = (int32_t)roundf(y3 * inv_scale_out);
            
            if (v0 < activation_min) v0 = activation_min;
            else if (v0 > activation_max) v0 = activation_max;
            if (v1 < activation_min) v1 = activation_min;
            else if (v1 > activation_max) v1 = activation_max;
            if (v2 < activation_min) v2 = activation_min;
            else if (v2 > activation_max) v2 = activation_max;
            if (v3 < activation_min) v3 = activation_min;
            else if (v3 > activation_max) v3 = activation_max;
            
            output[i+0] = (int8_t)v0;
            output[i+1] = (int8_t)v1;
            output[i+2] = (int8_t)v2;
            output[i+3] = (int8_t)v3;
        }
        for (; i < n; ++i) {
            float f = input[i] * scale_in;
            float scaled_f = f * sqrt_half;
            float erf_val = erff(scaled_f);
            float y = 0.5f * f * (1.0f + erf_val);
            int32_t v = (int32_t)roundf(y * inv_scale_out);
            if (v < activation_min) v = activation_min;
            else if (v > activation_max) v = activation_max;
            output[i] = (int8_t)v;
        }
        return;
    }

    uint32_t seen[8] = {0};
    for (int i = 0; i < n; ++i) {
        uint8_t v = (uint8_t)input[i];
        seen[v >> 5] |= (1U << (v & 31U));
    }

    int8_t table[256];
    for (int idx = 0; idx < 256; ++idx) {
        if (seen[idx >> 5] & (1U << (idx & 31U))) {
            float f = ((int8_t)idx) * scale_in;
            float scaled_f = f * sqrt_half;
            float erf_val = erff(scaled_f);
            float y = 0.5f * f * (1.0f + erf_val);
            int32_t v = (int32_t)roundf(y * inv_scale_out);
            if (v < activation_min) v = activation_min;
            else if (v > activation_max) v = activation_max;
            table[idx] = (int8_t)v;
        }
    }

    int i = 0;
    for (; i + 4 <= n; i += 4) {
        uint8_t v0 = (uint8_t)input[i+0];
        uint8_t v1 = (uint8_t)input[i+1];
        uint8_t v2 = (uint8_t)input[i+2];
        uint8_t v3 = (uint8_t)input[i+3];
        
        output[i+0] = table[v0];
        output[i+1] = table[v1];
        output[i+2] = table[v2];
        output[i+3] = table[v3];
    }
    for (; i < n; ++i) {
        uint8_t v = (uint8_t)input[i];
        output[i] = table[v];
    }
}