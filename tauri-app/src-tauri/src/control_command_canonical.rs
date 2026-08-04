// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

//! `control-command-canonical-v1`, shared byte-for-byte with Python and TS.

use ring::digest::{digest, SHA256};
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use std::collections::BTreeMap;
use std::fmt;

pub const CONTROL_COMMAND_SCHEMA: &str = "control-command-canonical-v1";
const CONTROL_DOMAIN: &[u8] = b"control-command-canonical-v1\0";
const CREDENTIAL_DOMAIN: &[u8] = b"window-control-credential-v1\0";
const MAX_SAFE_INTEGER: i64 = (1_i64 << 53) - 1;

#[derive(Clone, Debug, PartialEq)]
pub enum CanonicalValue {
    Null,
    Bool(bool),
    Integer(i64),
    String(String),
    Array(Vec<CanonicalValue>),
    Object(BTreeMap<Vec<u8>, (String, CanonicalValue)>),
}

impl<'de> Deserialize<'de> for CanonicalValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        struct CanonicalVisitor;
        impl<'de> Visitor<'de> for CanonicalVisitor {
            type Value = CanonicalValue;
            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a canonical JSON value")
            }
            fn visit_unit<E>(self) -> Result<Self::Value, E> {
                Ok(CanonicalValue::Null)
            }
            fn visit_none<E>(self) -> Result<Self::Value, E> {
                Ok(CanonicalValue::Null)
            }
            fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
                Ok(CanonicalValue::Bool(value))
            }
            fn visit_i64<E: de::Error>(self, value: i64) -> Result<Self::Value, E> {
                if value.abs() > MAX_SAFE_INTEGER {
                    return Err(E::custom("integer_outside_safe_range"));
                }
                Ok(CanonicalValue::Integer(value))
            }
            fn visit_u64<E: de::Error>(self, value: u64) -> Result<Self::Value, E> {
                if value > MAX_SAFE_INTEGER as u64 {
                    return Err(E::custom("integer_outside_safe_range"));
                }
                Ok(CanonicalValue::Integer(value as i64))
            }
            fn visit_f64<E: de::Error>(self, _value: f64) -> Result<Self::Value, E> {
                Err(E::custom("float_not_allowed"))
            }
            fn visit_str<E: de::Error>(self, value: &str) -> Result<Self::Value, E> {
                Ok(CanonicalValue::String(value.to_owned()))
            }
            fn visit_string<E: de::Error>(self, value: String) -> Result<Self::Value, E> {
                Ok(CanonicalValue::String(value))
            }
            fn visit_seq<A: SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> Result<Self::Value, A::Error> {
                let mut result = Vec::new();
                while let Some(value) = seq.next_element::<CanonicalValue>()? {
                    result.push(value);
                }
                Ok(CanonicalValue::Array(result))
            }
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut map: A,
            ) -> Result<Self::Value, A::Error> {
                let mut result = BTreeMap::new();
                while let Some((key, value)) =
                    map.next_entry::<String, CanonicalValue>()?
                {
                    let encoded = key.as_bytes().to_vec();
                    if result.insert(encoded, (key, value)).is_some() {
                        return Err(de::Error::custom("duplicate_object_key"));
                    }
                }
                Ok(CanonicalValue::Object(result))
            }
        }
        deserializer.deserialize_any(CanonicalVisitor)
    }
}

pub fn parse_canonical_json(raw: &str) -> Result<CanonicalValue, String> {
    if contains_negative_zero_token(raw.as_bytes()) {
        return Err("negative_zero".into());
    }
    let mut decoder = serde_json::Deserializer::from_str(raw);
    let value = CanonicalValue::deserialize(&mut decoder).map_err(|e| e.to_string())?;
    decoder.end().map_err(|e| e.to_string())?;
    Ok(value)
}

fn contains_negative_zero_token(raw: &[u8]) -> bool {
    let mut in_string = false;
    let mut escaped = false;
    for index in 0..raw.len().saturating_sub(1) {
        let byte = raw[index];
        if in_string {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                in_string = false;
            }
            continue;
        }
        if byte == b'"' {
            in_string = true;
            continue;
        }
        if byte != b'-' || raw[index + 1] != b'0' {
            continue;
        }
        let before_ok = index == 0
            || raw[index - 1].is_ascii_whitespace()
            || matches!(raw[index - 1], b'[' | b',' | b':');
        let after_index = index + 2;
        let after_ok = after_index == raw.len()
            || raw[after_index].is_ascii_whitespace()
            || matches!(raw[after_index], b']' | b'}' | b',');
        if before_ok && after_ok {
            return true;
        }
    }
    false
}

fn put_u32(target: &mut Vec<u8>, value: usize) -> Result<(), String> {
    let value = u32::try_from(value).map_err(|_| "length_or_count_overflow")?;
    target.extend_from_slice(&value.to_be_bytes());
    Ok(())
}

fn put_lp(target: &mut Vec<u8>, value: &str) -> Result<(), String> {
    put_u32(target, value.len())?;
    target.extend_from_slice(value.as_bytes());
    Ok(())
}

pub fn parse_canonical_u64(value: &str, field: &str) -> Result<u64, String> {
    let canonical = value == "0"
        || (!value.starts_with('0') && value.bytes().all(|byte| byte.is_ascii_digit()));
    if !canonical || value.is_empty() {
        return Err(format!("{field}:non_canonical_u64"));
    }
    value
        .parse::<u64>()
        .map_err(|_| format!("{field}:u64_overflow"))
}

pub fn encode_control_body(value: &CanonicalValue) -> Result<Vec<u8>, String> {
    let mut result = Vec::new();
    match value {
        CanonicalValue::Null => result.push(0),
        CanonicalValue::Bool(false) => result.push(1),
        CanonicalValue::Bool(true) => result.push(2),
        CanonicalValue::Integer(number) => {
            if number.abs() > MAX_SAFE_INTEGER {
                return Err("integer_outside_safe_range".into());
            }
            result.push(3);
            result.extend_from_slice(&number.to_be_bytes());
        }
        CanonicalValue::String(value) => {
            result.push(4);
            put_u32(&mut result, value.len())?;
            result.extend_from_slice(value.as_bytes());
        }
        CanonicalValue::Array(values) => {
            result.push(5);
            put_u32(&mut result, values.len())?;
            for value in values {
                result.extend_from_slice(&encode_control_body(value)?);
            }
        }
        CanonicalValue::Object(values) => {
            result.push(6);
            put_u32(&mut result, values.len())?;
            for (_encoded, (key, value)) in values {
                result.extend_from_slice(&encode_control_body(&CanonicalValue::String(
                    key.clone(),
                ))?);
                result.extend_from_slice(&encode_control_body(value)?);
            }
        }
    }
    Ok(result)
}

pub fn canonical_request_bytes(
    command_kind: &str,
    request_seq: &str,
    binding_epoch: &str,
    body: &CanonicalValue,
) -> Result<Vec<u8>, String> {
    debug_assert_eq!(
        CONTROL_DOMAIN,
        [CONTROL_COMMAND_SCHEMA.as_bytes(), b"\0"].concat()
    );
    let mut result = CONTROL_DOMAIN.to_vec();
    put_lp(&mut result, command_kind)?;
    result.extend_from_slice(
        &parse_canonical_u64(request_seq, "request_seq")?.to_be_bytes(),
    );
    result.extend_from_slice(
        &parse_canonical_u64(binding_epoch, "binding_epoch")?.to_be_bytes(),
    );
    result.extend_from_slice(&encode_control_body(body)?);
    Ok(result)
}

pub fn canonical_request_hash(
    command_kind: &str,
    request_seq: &str,
    binding_epoch: &str,
    body: &CanonicalValue,
) -> Result<String, String> {
    Ok(hex_encode(
        digest(
            &SHA256,
            &canonical_request_bytes(command_kind, request_seq, binding_epoch, body)?,
        )
        .as_ref(),
    ))
}

#[allow(clippy::too_many_arguments)]
pub fn credential_signed_payload(
    backend_process_instance_id: &str,
    connection_id: &str,
    control_epoch: &str,
    window_label: &str,
    scope: &str,
    challenge_hash: &str,
    request_seq: &str,
    command_kind: &str,
    canonical_request_hash_hex: &str,
    nonce: &str,
    issued_at: &str,
    expires_at: &str,
) -> Result<Vec<u8>, String> {
    let request_hash = hex_decode(canonical_request_hash_hex)?;
    if request_hash.len() != 32 || canonical_request_hash_hex.to_ascii_lowercase() != canonical_request_hash_hex {
        return Err("canonical_request_hash:not_lower_hex_sha256".into());
    }
    let mut result = CREDENTIAL_DOMAIN.to_vec();
    put_lp(&mut result, backend_process_instance_id)?;
    put_lp(&mut result, connection_id)?;
    result.extend_from_slice(
        &parse_canonical_u64(control_epoch, "control_epoch")?.to_be_bytes(),
    );
    put_lp(&mut result, window_label)?;
    put_lp(&mut result, scope)?;
    put_lp(&mut result, challenge_hash)?;
    result.extend_from_slice(
        &parse_canonical_u64(request_seq, "request_seq")?.to_be_bytes(),
    );
    put_lp(&mut result, command_kind)?;
    result.extend_from_slice(&request_hash);
    put_lp(&mut result, nonce)?;
    result.extend_from_slice(&parse_canonical_u64(issued_at, "issued_at")?.to_be_bytes());
    result.extend_from_slice(&parse_canonical_u64(expires_at, "expires_at")?.to_be_bytes());
    Ok(result)
}

pub fn sha256_hex(value: &[u8]) -> String {
    hex_encode(digest(&SHA256, value).as_ref())
}

pub fn hex_encode(value: &[u8]) -> String {
    value.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub fn hex_decode(value: &str) -> Result<Vec<u8>, String> {
    if value.len() % 2 != 0 {
        return Err("invalid_hex".into());
    }
    (0..value.len())
        .step_by(2)
        .map(|index| {
            u8::from_str_radix(&value[index..index + 2], 16)
                .map_err(|_| "invalid_hex".to_string())
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use ring::signature::{Ed25519KeyPair, KeyPair, UnparsedPublicKey, ED25519};
    use serde_json::Value;
    use std::fs;
    use std::path::PathBuf;

    #[test]
    fn duplicate_keys_and_float_are_rejected() {
        assert!(parse_canonical_json(r#"{"a":1,"a":2}"#).is_err());
        assert!(parse_canonical_json("1.5").is_err());
        assert!(parse_canonical_json("-0").is_err());
    }

    fn fixture() -> Value {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/control-command-canonical-v1.json");
        serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
    }

    #[test]
    fn rust_matches_all_shared_canonical_vectors() {
        let vectors = fixture();
        for item in vectors["valid"].as_array().unwrap() {
            let body = parse_canonical_json(item["raw_body_json"].as_str().unwrap()).unwrap();
            assert_eq!(
                hex_encode(&encode_control_body(&body).unwrap()),
                item["tlv_hex"].as_str().unwrap()
            );
            assert_eq!(
                canonical_request_hash(
                    item["command_kind"].as_str().unwrap(),
                    item["request_seq"].as_str().unwrap(),
                    item["binding_epoch"].as_str().unwrap(),
                    &body,
                )
                .unwrap(),
                item["request_hash"].as_str().unwrap()
            );
            if let Some(equivalent) = item["equivalent_raw_body_json"].as_str() {
                assert_eq!(
                    encode_control_body(&parse_canonical_json(equivalent).unwrap()).unwrap(),
                    encode_control_body(&body).unwrap()
                );
            }
        }
        for item in vectors["reject_raw_json"].as_array().unwrap() {
            assert!(
                parse_canonical_json(item["raw"].as_str().unwrap()).is_err(),
                "accepted reject vector {}",
                item["name"]
            );
        }
        for item in vectors["reject_u64"].as_array().unwrap() {
            assert!(parse_canonical_u64(item.as_str().unwrap(), "fixture").is_err());
        }
        for item in vectors["reject_utf8_hex"].as_array().unwrap() {
            let bytes = hex_decode(item.as_str().unwrap()).unwrap();
            assert!(String::from_utf8(bytes).is_err());
        }
    }

    #[test]
    fn rust_matches_shared_ed25519_golden() {
        let vectors = fixture();
        let golden = &vectors["credential_golden"];
        let body = parse_canonical_json(golden["raw_body_json"].as_str().unwrap()).unwrap();
        let request_hash = canonical_request_hash(
            golden["command_kind"].as_str().unwrap(),
            golden["request_seq"].as_str().unwrap(),
            golden["binding_epoch"].as_str().unwrap(),
            &body,
        )
        .unwrap();
        assert_eq!(
            request_hash,
            golden["canonical_request_hash"].as_str().unwrap()
        );
        let payload = credential_signed_payload(
            golden["backend_process_instance_id"].as_str().unwrap(),
            golden["connection_id"].as_str().unwrap(),
            golden["control_epoch"].as_str().unwrap(),
            golden["window_label"].as_str().unwrap(),
            golden["scope"].as_str().unwrap(),
            golden["challenge_hash"].as_str().unwrap(),
            golden["request_seq"].as_str().unwrap(),
            golden["command_kind"].as_str().unwrap(),
            &request_hash,
            golden["nonce"].as_str().unwrap(),
            golden["issued_at"].as_str().unwrap(),
            golden["expires_at"].as_str().unwrap(),
        )
        .unwrap();
        assert_eq!(hex_encode(&payload), golden["payload_hex"].as_str().unwrap());
        let pair = Ed25519KeyPair::from_seed_unchecked(
            &hex_decode(golden["private_seed_hex"].as_str().unwrap()).unwrap(),
        )
        .unwrap();
        assert_eq!(
            hex_encode(pair.public_key().as_ref()),
            golden["public_key_hex"].as_str().unwrap()
        );
        let signature = pair.sign(&payload);
        assert_eq!(
            hex_encode(signature.as_ref()),
            golden["signature_hex"].as_str().unwrap()
        );
        UnparsedPublicKey::new(&ED25519, pair.public_key().as_ref())
            .verify(&payload, signature.as_ref())
            .unwrap();
    }
}
