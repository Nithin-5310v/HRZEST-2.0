// Employee PII (name, email, phone, role, department) used to sit in plain
// AsyncStorage -- unencrypted on both platforms, readable off a rooted/
// jailbroken device or extracted from a plain device backup. Routed through
// secureStorage.js instead: Keychain/Keystore-backed on native builds (the
// same wrapper AuthContext.js already uses for the session token/user
// object), AsyncStorage only as its documented web fallback (no Keychain/
// Keystore in a browser).
import { secureGetItem, secureSetItem, secureRemoveItem } from './secureStorage';

const STORAGE_KEY = '@custom_created_employees_v1';

// Kept only to remove it: the app no longer keeps its own copy of the
// staff list (it kept employees deleted on the website, and duplicated
// renumbered ones). clearLocalEmployees() wipes what older versions saved.
const saveLocalEmployee = async (employeeObj) => {
  try {
    const existingStr = await secureGetItem(STORAGE_KEY);
    let list = existingStr ? JSON.parse(existingStr) : [];
    const empId = employeeObj.employee_id || employeeObj.id;
    list = list.filter((e) => (e.employee_id || e.id) !== empId);
    list.unshift(employeeObj);
    await secureSetItem(STORAGE_KEY, JSON.stringify(list));
    return list;
  } catch (e) {
    return [];
  }
};

export const deleteLocalEmployee = async (empId) => {
  try {
    const existingStr = await secureGetItem(STORAGE_KEY);
    let list = existingStr ? JSON.parse(existingStr) : [];
    list = list.filter((e) => (e.employee_id || e.id) !== empId);
    await secureSetItem(STORAGE_KEY, JSON.stringify(list));
    return list;
  } catch (e) {
    return [];
  }
};

export const getLocalEmployees = async () => {
  try {
    const existingStr = await secureGetItem(STORAGE_KEY);
    return existingStr ? JSON.parse(existingStr) : [];
  } catch (e) {
    return [];
  }
};

export const clearLocalEmployees = async () => {
  try {
    await secureRemoveItem(STORAGE_KEY);
  } catch (e) {
    // best-effort -- nothing else to fall back to
  }
};

export const mergeEmployeesWithLocal = async (serverEmployees = []) => {
  try {
    const localList = await getLocalEmployees();
    const merged = [...localList];
    serverEmployees.forEach((serverEmp) => {
      const idx = merged.findIndex(
        (e) => (e.employee_id || e.id) === (serverEmp.employee_id || serverEmp.id)
      );
      if (idx !== -1) {
        merged[idx] = { ...merged[idx], ...serverEmp };
      } else {
        merged.push(serverEmp);
      }
    });
    return merged;
  } catch (e) {
    return serverEmployees;
  }
};
