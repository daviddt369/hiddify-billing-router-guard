<?php
/**
 * WordPress REST API proxy for the Web Trial endpoint.
 *
 * Paste into WPCode (or functions.php). Solves two problems:
 *   1. Panel domain may be blocked by ISPs — server-to-server request bypasses blocks.
 *   2. Rate limiting is enforced here using WordPress transients (DB-backed,
 *      shared across all PHP workers) keyed on the real client IP.
 *
 * CONFIG — set these two constants before activating:
 */
define('VPN_TRIAL_ENDPOINT', 'https://YOUR_PANEL_DOMAIN/YOUR_PROXY_PATH/api/v2/trial/');
define('VPN_TRIAL_RATE_MAX', 2);   // max requests per IP per hour

add_action('rest_api_init', function () {
    register_rest_route('vpn/v1', '/trial', [
        'methods'             => ['POST', 'OPTIONS'],
        'callback'            => 'vpn_trial_proxy',
        'permission_callback' => '__return_true',
    ]);
});

function vpn_trial_get_client_ip() {
    // Prefer Cloudflare header if the WordPress host is behind Cloudflare.
    if (!empty($_SERVER['HTTP_CF_CONNECTING_IP'])) {
        return $_SERVER['HTTP_CF_CONNECTING_IP'];
    }
    if (!empty($_SERVER['HTTP_X_FORWARDED_FOR'])) {
        $parts = explode(',', $_SERVER['HTTP_X_FORWARDED_FOR']);
        return trim($parts[0]);
    }
    return $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0';
}

function vpn_trial_proxy($request) {
    if ($request->get_method() === 'OPTIONS') {
        return new WP_REST_Response(null, 200);
    }

    $clientIp = vpn_trial_get_client_ip();

    // Fixed-window rate limit (window resets every clock hour).
    $bucket  = floor(time() / 3600);
    $rateKey = 'vpn_rl_' . md5($clientIp . '_' . $bucket);
    $count   = (int) get_transient($rateKey);
    if ($count >= VPN_TRIAL_RATE_MAX) {
        return new WP_REST_Response(
            ['status' => 'error', 'message' => 'Слишком много запросов. Попробуйте через час.'],
            429
        );
    }
    set_transient($rateKey, $count + 1, 7200); // TTL covers two clock windows

    try {
        $body    = $request->get_body();
        $headers = [
            'Content-Type: application/json',
            'X-Real-IP: '       . $clientIp,
            'X-Forwarded-For: ' . $clientIp,
        ];

        $ch = curl_init(VPN_TRIAL_ENDPOINT);
        curl_setopt_array($ch, [
            CURLOPT_POST           => true,
            CURLOPT_POSTFIELDS     => $body,
            CURLOPT_HTTPHEADER     => $headers,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_SSL_VERIFYPEER => false,
            CURLOPT_TIMEOUT        => 20,
        ]);
        $result   = curl_exec($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $curlErr  = curl_error($ch);
        curl_close($ch);

        if ($result === false) {
            return new WP_REST_Response(
                ['status' => 'error', 'message' => 'curl: ' . $curlErr],
                503
            );
        }

        $json = json_decode($result, true);
        return new WP_REST_Response(
            $json !== null ? $json : ['status' => 'error', 'raw' => substr($result, 0, 300)],
            $httpCode ?: 502
        );

    } catch (\Throwable $e) {
        return new WP_REST_Response(
            ['status' => 'error', 'message' => $e->getMessage()],
            500
        );
    }
}
