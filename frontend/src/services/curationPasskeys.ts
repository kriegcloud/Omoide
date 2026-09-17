import type {
  CurationAssertionOptions, CurationPasskeyCredential, CurationRegistrationOptions,
} from "../types/curation";

/** Safe display messages only: browser exceptions can contain authenticator details. */
export class CurationPasskeyError extends Error {
  constructor(public cancelled: boolean, message: string) {
    super(message);
    this.name = "CurationPasskeyError";
  }
}

export function decodePasskeyBytes(value: string): ArrayBuffer {
  if (!value || !/^[A-Za-z0-9_-]+$/.test(value) || value.length % 4 === 1) {
    throw new CurationPasskeyError(false, "The passkey request is invalid. Refresh the review before continuing.");
  }
  const bytes = Uint8Array.from(atob(value.replace(/-/g, "+").replace(/_/g, "/")), char => char.charCodeAt(0));
  if (encodePasskeyBytes(bytes.buffer) !== value) {
    throw new CurationPasskeyError(false, "The passkey request is invalid. Refresh the review before continuing.");
  }
  return bytes.buffer;
}

export function encodePasskeyBytes(value: ArrayBuffer): string {
  let binary = "";
  for (const byte of new Uint8Array(value)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function registrationOptions(value: CurationRegistrationOptions): PublicKeyCredentialCreationOptions {
  if (value.authenticatorSelection?.userVerification !== "required") {
    throw new CurationPasskeyError(false, "This passkey request does not require user verification. Refresh the review before continuing.");
  }
  return {
    ...value,
    challenge: decodePasskeyBytes(value.challenge),
    user: { ...value.user, id: decodePasskeyBytes(value.user.id) },
    excludeCredentials: value.excludeCredentials?.map(entry => ({ ...entry, id: decodePasskeyBytes(entry.id) })),
  };
}

export function assertionOptions(value: CurationAssertionOptions): PublicKeyCredentialRequestOptions {
  if (value.userVerification !== "required") {
    throw new CurationPasskeyError(false, "This passkey request does not require user verification. Refresh the review before continuing.");
  }
  return {
    ...value,
    challenge: decodePasskeyBytes(value.challenge),
    allowCredentials: value.allowCredentials?.map(entry => ({ ...entry, id: decodePasskeyBytes(entry.id) })),
  };
}

function serializeExtensions(value: unknown): unknown {
  if (value instanceof ArrayBuffer) return encodePasskeyBytes(value);
  if (ArrayBuffer.isView(value)) return encodePasskeyBytes(new Uint8Array(value.buffer, value.byteOffset, value.byteLength).slice().buffer);
  if (Array.isArray(value)) return value.map(serializeExtensions);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, serializeExtensions(entry)]));
  return value;
}

export function serializePasskey(value: PublicKeyCredential, kind: "registration" | "assertion"): CurationPasskeyCredential {
  if (value.type !== "public-key" || !value.rawId || !value.response?.clientDataJSON) {
    throw new CurationPasskeyError(false, "The browser did not return a valid passkey response. No review was submitted.");
  }
  const common = { clientDataJSON: encodePasskeyBytes(value.response.clientDataJSON) };
  let response: CurationPasskeyCredential["response"];
  if (kind === "registration") {
    const attestation = value.response as AuthenticatorAttestationResponse;
    if (!attestation.attestationObject) throw new CurationPasskeyError(false, "The browser did not return a passkey registration.");
    response = { ...common, attestationObject: encodePasskeyBytes(attestation.attestationObject), transports: attestation.getTransports?.() ?? [] };
  } else {
    const assertion = value.response as AuthenticatorAssertionResponse;
    if (!assertion.authenticatorData || !assertion.signature) throw new CurationPasskeyError(false, "The browser did not return a passkey confirmation. No review was submitted.");
    response = {
      ...common, authenticatorData: encodePasskeyBytes(assertion.authenticatorData),
      signature: encodePasskeyBytes(assertion.signature),
      userHandle: assertion.userHandle == null ? null : encodePasskeyBytes(assertion.userHandle),
    };
  }
  return {
    id: value.id, rawId: encodePasskeyBytes(value.rawId), type: "public-key",
    ...(value.authenticatorAttachment ? { authenticatorAttachment: value.authenticatorAttachment } : {}),
    clientExtensionResults: serializeExtensions(value.getClientExtensionResults()), response,
  };
}

async function ceremony(run: () => Promise<Credential | null>, kind: "registration" | "assertion"): Promise<CurationPasskeyCredential> {
  try {
    if (typeof navigator === "undefined" || !navigator.credentials || !globalThis.isSecureContext) {
      throw new CurationPasskeyError(false, "Passkeys need a supported browser and a secure connection. Open this review over HTTPS or on localhost.");
    }
    const credential = await run();
    if (!credential) throw new CurationPasskeyError(true, "Passkey confirmation was cancelled. No review was submitted. Your draft and decision are kept.");
    return serializePasskey(credential as PublicKeyCredential, kind);
  } catch (reason) {
    if (reason instanceof CurationPasskeyError) throw reason;
    const name = reason instanceof Error ? reason.name : "";
    if (name === "NotAllowedError" || name === "AbortError") {
      throw new CurationPasskeyError(true, "Passkey confirmation was cancelled or timed out. No review was submitted. Your draft and decision are kept.");
    }
    throw new CurationPasskeyError(false, "The passkey could not be verified by this browser. No review was submitted. Check the registered passkey and site address before trying again.");
  }
}

export const registerCurationPasskey = (options: CurationRegistrationOptions, signal?: AbortSignal) =>
  ceremony(() => navigator.credentials.create({ publicKey: registrationOptions(options), signal }), "registration");

export const confirmCurationPasskey = (options: CurationAssertionOptions, signal?: AbortSignal) =>
  ceremony(() => navigator.credentials.get({ publicKey: assertionOptions(options), signal }), "assertion");
