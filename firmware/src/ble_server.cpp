#include "ble_server.h"

#include <Arduino.h>
#include <NimBLEDevice.h>

namespace {

// UsageState outgrew the old 64-byte MTU (payload = MTU - 3) once the real
// quota percentages were added. 247 is the common BLE 4.2+ ceiling most
// stacks grant; the negotiated value is printed in onMTUChange because
// docs/handover.md #4 is right that it must never be assumed.
constexpr uint16_t kPreferredMtu = 247;

NimBLEServer *server = nullptr;
NimBLECharacteristic *usageChar = nullptr;
NimBLECharacteristic *timeChar = nullptr;

// Shared between the NimBLE host task (callbacks below) and loop() on the Arduino task.
// Callbacks must not block, so they just copy the incoming struct behind a short
// critical section and set a flag; all real work happens in loop().
portMUX_TYPE stateMux = portMUX_INITIALIZER_UNLOCKED;
UsageState latestState{};
volatile bool stateDirty = false;

// Wall clock: epoch seconds at the instant millis() == syncMillis. Set by the
// Time Sync characteristic, which the host writes on every connect.
volatile uint32_t syncEpoch = 0;
volatile uint32_t syncMillis = 0;

class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *srv, NimBLEConnInfo &info) override {
    Serial.printf("[BLE] connected: %s\n", info.getAddress().toString().c_str());
  }

  void onDisconnect(NimBLEServer *srv, NimBLEConnInfo &info, int reason) override {
    Serial.printf("[BLE] disconnected (reason %d), resuming advertising\n", reason);
    NimBLEDevice::startAdvertising();
  }

  void onMTUChange(uint16_t mtu, NimBLEConnInfo &info) override {
    // handover.md #4: never assume the requested MTU was granted - verify it here.
    Serial.printf("[BLE] MTU negotiated: %u (requested %u)\n", mtu, kPreferredMtu);
  }
};

class UsageStateCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic *chr, NimBLEConnInfo &info) override {
    NimBLEAttValue val = chr->getValue();
    if (val.length() != sizeof(UsageState)) {
      Serial.printf("[BLE] UsageState write ignored: got %u bytes, want %u\n", val.length(),
                     (unsigned)sizeof(UsageState));
      return;
    }

    UsageState incoming = val.getValue<UsageState>();
    if (incoming.version != USAGE_STATE_VERSION) {
      // handover.md #4: reject unknown version rather than misparsing.
      Serial.printf("[BLE] UsageState write rejected: unknown version %u\n", incoming.version);
      return;
    }

    portENTER_CRITICAL(&stateMux);
    latestState = incoming;
    stateDirty = true;
    portEXIT_CRITICAL(&stateMux);
  }
};

class TimeSyncCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic *chr, NimBLEConnInfo &info) override {
    NimBLEAttValue val = chr->getValue();
    if (val.length() != sizeof(uint32_t)) {
      Serial.printf("[BLE] TimeSync write ignored: got %u bytes, want 4\n", val.length());
      return;
    }

    uint32_t epoch = val.getValue<uint32_t>();
    portENTER_CRITICAL(&stateMux);
    syncEpoch = epoch;
    syncMillis = millis();
    portEXIT_CRITICAL(&stateMux);
    Serial.printf("[BLE] time synced: epoch=%lu\n", (unsigned long)epoch);
  }
};

ServerCallbacks serverCallbacks;
UsageStateCallbacks usageCallbacks;
TimeSyncCallbacks timeCallbacks;

}  // namespace

void ble_server_init() {
  NimBLEDevice::init("esp32-claude");
  NimBLEDevice::setMTU(kPreferredMtu);

  server = NimBLEDevice::createServer();
  server->setCallbacks(&serverCallbacks);

  NimBLEService *service = server->createService(SERVICE_UUID);

  // Read: lets nRF Connect (or similar) inspect the last value manually while bringing
  // the board up (handover.md build order step 3). Write: the host pushes fresh data -
  // NOT Notify, since nothing needs push updates *from* the device for this value.
  usageChar = service->createCharacteristic(USAGE_STATE_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE);
  usageChar->setCallbacks(&usageCallbacks);
  usageChar->setValue<UsageState>(UsageState{});

  timeChar = service->createCharacteristic(TIME_SYNC_UUID, NIMBLE_PROPERTY::WRITE);
  timeChar->setCallbacks(&timeCallbacks);

  NimBLEAdvertising *advertising = NimBLEDevice::getAdvertising();
  advertising->setName("esp32-claude");
  advertising->addServiceUUID(service->getUUID());
  advertising->enableScanResponse(true);
  advertising->start();

  Serial.println("[BLE] advertising as \"esp32-claude\"");
}

bool ble_server_is_connected() { return server != nullptr && server->getConnectedCount() > 0; }

bool ble_server_take_new_state(UsageState &out) {
  bool has = false;
  portENTER_CRITICAL(&stateMux);
  if (stateDirty) {
    out = latestState;
    stateDirty = false;
    has = true;
  }
  portEXIT_CRITICAL(&stateMux);
  return has;
}

uint32_t ble_server_synced_epoch() {
  uint32_t epoch, base;
  portENTER_CRITICAL(&stateMux);
  epoch = syncEpoch;
  base = syncMillis;
  portEXIT_CRITICAL(&stateMux);
  if (epoch == 0) return 0;
  return epoch + (millis() - base) / 1000;
}
