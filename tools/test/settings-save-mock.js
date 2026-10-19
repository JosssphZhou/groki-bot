// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0

(async () => {
  // BLE boundary substitute for the settings page save tests.
  // The real page, Save handler, disconnect handler and WebCrypto still run.
  const state = window.__settingsSaveMock = {
    language: 'en', pending: null, provider: 0, pendingProvider: null,
    writes: [], applied: 0, failProvider: false, failRead: false,
    failApplyWrite: false, applyAttempts: 0,
  };
  async function decode(wire) {
    return new Uint8Array(await crypto.subtle.decrypt(
      {name: 'AES-GCM', iv: wire.subarray(0, 12)}, aesKey, wire.subarray(12)));
  }
  async function encode(plain) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const encrypted = new Uint8Array(await crypto.subtle.encrypt({name: 'AES-GCM', iv}, aesKey, plain));
    const wire = new Uint8Array(12 + encrypted.length);
    wire.set(iv); wire.set(encrypted, 12);
    return new DataView(wire.buffer);
  }
  state.disconnect = () => {
    bleDevice.gatt.connected = false;
    bleDevice.dispatchEvent(new Event('gattserverdisconnected'));
  };
  state.armBeforeApplyDisconnect = () => {
    const encrypt = crypto.subtle.encrypt.bind(crypto.subtle);
    crypto.subtle.encrypt = async (algorithm, key, data) => {
      if (data.length === 1 && data[0] === 1) {
        crypto.subtle.encrypt = encrypt;
        state.disconnect();
        throw new Error('simulated disconnected during encryption, before Apply transport');
      }
      return encrypt(algorithm, key, data);
    };
  };
  state.connect = async () => {
    aesKey = await crypto.subtle.generateKey({name: 'AES-GCM', length: 256}, false, ['encrypt', 'decrypt']);
    bleDevice = new EventTarget();
    bleDevice.gatt = {connected: true};
    bleDevice.addEventListener('gattserverdisconnected', onDisconnected);
    chrDeviceLanguage = {
      async readValue() {
        if (state.failRead) throw new Error('simulated language read failure');
        return encode(new TextEncoder().encode(state.language));
      },
      async writeValueWithResponse(wire) {
        const language = new TextDecoder().decode(await decode(wire));
        if (!['en', 'zh'].includes(language)) throw new Error('invalid language');
        state.pending = language;
        state.writes.push({characteristic: CHR_DEVICE_LANGUAGE, language});
      },
    };
    chrProvider = {
      async readValue() { return encode(new Uint8Array([state.provider])); },
      async writeValueWithResponse(wire) {
        const provider = (await decode(wire))[0];
        if (state.failProvider) {
          state.disconnect();
          throw new Error('simulated disconnected after staging language, before provider');
        }
        state.pendingProvider = provider;
        state.writes.push({characteristic: CHR_PROVIDER, provider});
      },
    };
    chrApply = {
      async writeValueWithResponse(wire) {
        ++state.applyAttempts;
        const plain = await decode(wire);
        if (plain.length !== 1 || plain[0] !== 1) throw new Error('invalid Apply');
        if (state.failApplyWrite) {
          state.disconnect();
          throw new Error('simulated disconnected without an Apply write response');
        }
        state.language = state.pending ?? state.language;
        state.provider = state.pendingProvider ?? state.provider;
        state.pending = state.pendingProvider = null;
        ++state.applied;
        state.writes.push({characteristic: CHR_APPLY, apply: 1});
      },
    };
    await loadDeviceLanguage();
    const provider = (await readEncrypted(chrProvider))[0];
    const select = document.getElementById('provider');
    select.value = String(provider);
    select.dataset.initial = String(provider);
    setBleUi(true);
  };
  await state.connect();
  log('QA: simulated BLE and reboot only; no robot connected.');
})()
