(function (global) {
  'use strict';

  function text(value) {
    return String(value == null ? '' : value).trim();
  }

  function looksLikeRawUrl(value) {
    return /^https?:\/\//i.test(text(value));
  }

  function safePhoneForDisplay(value) {
    return looksLikeRawUrl(value) ? '' : text(value);
  }

  function isDegradedResult(result) {
    const packet = result && typeof result === 'object' ? result.public_packet : null;
    return !!(result && (result.degraded_sources === true || (packet && packet.degraded === true)));
  }

  function degradedReason(result) {
    const packet = result && typeof result === 'object' ? result.public_packet : null;
    return text((packet && packet.degraded_reason) || (result && (result.degraded_reason || result.confidence_reason)) || '');
  }

  global.PermitAssistRender = Object.freeze({
    looksLikeRawUrl,
    safePhoneForDisplay,
    isDegradedResult,
    degradedReason,
  });
})(window);
