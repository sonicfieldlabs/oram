import Foundation
import Security

final class KeychainStore {
    static let shared = KeychainStore()

    private let service = "wtf.momoto.oram"

    func setSecret(_ value: String, provider: String) throws {
        let data = Data(value.utf8)
        let query = baseQuery(provider: provider)
        SecItemDelete(query as CFDictionary)
        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(attributes as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw KeychainError.status(status)
        }
    }

    func getSecret(provider: String) throws -> String? {
        var query = baseQuery(provider: provider)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess, let data = item as? Data else {
            throw KeychainError.status(status)
        }
        return String(data: data, encoding: .utf8)
    }

    func deleteSecret(provider: String) throws {
        let status = SecItemDelete(baseQuery(provider: provider) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError.status(status)
        }
    }

    func hasSecret(provider: String) -> Bool {
        (try? getSecret(provider: provider)) != nil
    }

    private func baseQuery(provider: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: "provider:\(provider)"
        ]
    }
}

enum KeychainError: Error, LocalizedError {
    case status(OSStatus)

    var errorDescription: String? {
        switch self {
        case let .status(status):
            return "Keychain error \(status)"
        }
    }
}
