import React, { useState, useEffect } from "react";
import {
  ScrollView,
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
  Modal,
  TextInput,
  Alert,
  Image,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import { DrawerActions } from "@react-navigation/native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

let ImagePicker;
try {
  ImagePicker = require("expo-image-picker");
} catch (_) {
  ImagePicker = null;
}

import AdminHeader from "../../components/admin/AdminHeader";
import AdminSearchBar from "../../components/admin/AdminSearchBar";
import {
  fetchEmployees, addEmployee, uploadEmployeePhoto, getPhotoUrl, fetchBillingStatus,
  editEmployee, deleteEmployee, sendEmailBlast,
} from "../../api/client";
import { saveLocalEmployee, mergeEmployeesWithLocal, deleteLocalEmployee } from "../../utils/employeeStore";
import { useTheme } from "../../store/ThemeContext";

import SaasFilterSheet from "../../components/common/SaasFilterSheet";

export default function EmployeesScreen({ navigation }) {
  const { colors } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [search, setSearch] = useState("");
  const [selectedDept, setSelectedDept] = useState("All");
  const [selectedStatus, setSelectedStatus] = useState("All");
  const [selectedSort, setSelectedSort] = useState("Name (A-Z)");
  const [filterModalVisible, setFilterModalVisible] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [employees, setEmployees] = useState([]);
  const [selectedEmp, setSelectedEmp] = useState(null);
  // Same paid_employee_slots cap the web app's employees.html banner shows
  // -- surfaced here too so Admin/HR see it right where they'd try to add
  // someone, not just after being blocked (see SeatsBillingScreen).
  const [billingStatus, setBillingStatus] = useState(null);

  // Add Employee Form State
  const [addModalVisible, setAddModalVisible] = useState(false);
  const [newEmpId, setNewEmpId] = useState("");
  const [newEmpName, setNewEmpName] = useState("");
  const [newEmpRole, setNewEmpRole] = useState("");
  const [newEmpDept, setNewEmpDept] = useState("");
  const [newEmpEmail, setNewEmpEmail] = useState("");
  const [newEmpPhone, setNewEmpPhone] = useState("");
  const [newEmpDoj, setNewEmpDoj] = useState(new Date().toISOString().split("T")[0]);
  const [newEmpPassword, setNewEmpPassword] = useState("");
  const [newEmpPhoto, setNewEmpPhoto] = useState(null);

  // Edit Employee Form State -- mirrors blueprints/employees.py's
  // api_edit_employee(), which only accepts name/email/role/date_of_joining.
  const [editModalVisible, setEditModalVisible] = useState(false);
  const [editEmpId, setEditEmpId] = useState("");
  const [editEmpName, setEditEmpName] = useState("");
  const [editEmpEmail, setEditEmpEmail] = useState("");
  const [editEmpRole, setEditEmpRole] = useState("");
  const [editEmpDoj, setEditEmpDoj] = useState("");
  const [editSubmitting, setEditSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Email Blast Form State
  const [blastModalVisible, setBlastModalVisible] = useState(false);
  const [blastTargetType, setBlastTargetType] = useState("all");
  const [blastTargetValue, setBlastTargetValue] = useState("");
  const [blastSubject, setBlastSubject] = useState("");
  const [blastBody, setBlastBody] = useState("");
  const [sendingBlast, setSendingBlast] = useState(false);

  const handleSendBlast = async () => {
    if (!blastSubject.trim() || !blastBody.trim()) {
      Alert.alert("Validation Error", "Subject and message body are required.");
      return;
    }
    if (blastTargetType !== "all" && !blastTargetValue.trim()) {
      Alert.alert("Validation Error", blastTargetType === "department" ? "Select a department." : "Enter an employee ID.");
      return;
    }
    setSendingBlast(true);
    let res;
    try {
      res = await sendEmailBlast(blastTargetType, blastTargetValue.trim(), blastSubject.trim(), blastBody.trim());
    } catch (e) {
      res = e?.response;
    }
    setSendingBlast(false);
    if (!res?.data?.ok) {
      Alert.alert("Send Failed", res?.data?.msg || "Could not send the broadcast email.");
      return;
    }
    setBlastModalVisible(false);
    setBlastSubject("");
    setBlastBody("");
    setBlastTargetValue("");
    setBlastTargetType("all");
    Alert.alert("Queued", res.data.msg);
  };

  const handleAutoGenerateId = () => {
    const nextSeq = 1001 + (employees ? employees.length : 0);
    setNewEmpId(`EMP-${nextSeq}`);
  };

  const handleQuickFill = () => {
    const nextSeq = 1001 + (employees ? employees.length : 0);
    const empId = `EMP-${nextSeq}`;
    setNewEmpId(empId);
    setNewEmpName("Ravi Kumar");
    setNewEmpEmail("ravikumar@company.com");
    setNewEmpPhone("9876543210");
    setNewEmpDoj(new Date().toISOString().split("T")[0]);
    setNewEmpDept("Engineering");
    setNewEmpRole("Senior Software Engineer");
    setNewEmpPassword("welcome123");
  };

  const handlePickPhoto = async () => {
    try {
      if (!ImagePicker) {
        Alert.alert("Module Initializing", "Photo library module is initializing. Please try again.");
        return;
      }
      const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!permission.granted) {
        Alert.alert("Permission Denied", "Permission to access media library is required to pick an employee photo.");
        return;
      }
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        allowsEditing: true,
        aspect: [1, 1],
        quality: 0.8,
      });
      if (!result.canceled && result.assets && result.assets.length > 0) {
        setNewEmpPhoto(result.assets[0].uri);
      }
    } catch (e) {
      Alert.alert("Error", "Could not pick photo.");
    }
  };

  const handleTakePhoto = async () => {
    try {
      if (!ImagePicker) {
        Alert.alert("Module Initializing", "Camera module is initializing. Please try again.");
        return;
      }
      const permission = await ImagePicker.requestCameraPermissionsAsync();
      if (!permission.granted) {
        Alert.alert("Permission Denied", "Permission to access camera is required to capture an employee photo.");
        return;
      }
      const result = await ImagePicker.launchCameraAsync({
        allowsEditing: true,
        aspect: [1, 1],
        quality: 0.8,
      });
      if (!result.canceled && result.assets && result.assets.length > 0) {
        setNewEmpPhoto(result.assets[0].uri);
      }
    } catch (e) {
      Alert.alert("Error", "Could not capture photo.");
    }
  };

  const loadData = async () => {
    try {
      const res = await fetchEmployees();
      let rawList = [];
      if (res && res.data) {
        if (Array.isArray(res.data.employees)) {
          rawList = res.data.employees;
        } else if (Array.isArray(res.data)) {
          rawList = res.data;
        } else if (Array.isArray(res.data.data)) {
          rawList = res.data.data;
        }
      }
      // Missing fields stay empty: the edit form is filled from these and
      // saves them back, so a made-up email, role or joining date here
      // would end up in the employee's real record.
      const formattedList = rawList.map((emp) => ({
        id: emp.employee_id || emp.id,
        employee_id: emp.employee_id || emp.id,
        name: emp.name || emp.employee_id || "Staff Member",
        email: emp.email || "",
        role: emp.role || emp.designation || "",
        department: emp.department || "",
        status: emp.status || "Active",
        phone: emp.phone || "",
        date_of_joining: emp.date_of_joining || emp.doj || "",
        has_photo: true,
      }));

      const mergedList = await mergeEmployeesWithLocal(formattedList);
      setEmployees(mergedList);
    } catch (e) {
      const storedLocal = await mergeEmployeesWithLocal([]);
      if (storedLocal.length > 0) {
        setEmployees(storedLocal);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  const loadBillingStatus = async () => {
    try {
      const res = await fetchBillingStatus();
      if (res?.data?.ok) setBillingStatus(res.data);
    } catch (e) {
      // Non-critical -- the banner just doesn't render without it.
    }
  };

  useEffect(() => {
    loadData();
    loadBillingStatus();
  }, []);

  const onRefresh = () => {
    setRefreshing(true);
    loadData();
    loadBillingStatus();
  };

  const handleAddEmployeeSubmit = async () => {
    const empIdTrim = newEmpId.trim().toUpperCase();
    const empNameTrim = newEmpName.trim();
    const empPassTrim = newEmpPassword.trim();
    if (!empIdTrim || !empNameTrim || !empPassTrim) {
      Alert.alert("Input Required", "Employee ID, Full Name and Password are required.");
      return;
    }
    setSubmitting(true);
    const payload = {
      employee_id: empIdTrim,
      name: empNameTrim,
      password: empPassTrim,
      role: newEmpRole.trim() || "Employee",
      department: newEmpDept.trim(),
      email: newEmpEmail.trim(),
      phone: newEmpPhone.trim() || "",
      date_of_joining: newEmpDoj.trim(),
    };

    // Real API call first -- only treat this as a success (and only cache
    // locally / show the success alert) once the server actually confirms
    // the account was created. Previously this added the employee to local
    // state and showed "Staff Registered" unconditionally, before the API
    // call was even awaited -- a failed signup (duplicate ID, validation
    // error) still left a phantom employee in the directory forever.
    let res;
    try {
      res = await addEmployee(payload);
    } catch (e) {
      res = e?.response;
    }

    if (!res?.data?.ok) {
      Alert.alert("Registration Failed", res?.data?.msg || "Could not register this employee. Check the details and try again.");
      setSubmitting(false);
      return;
    }

    const newStaffObj = {
      id: empIdTrim,
      employee_id: empIdTrim,
      name: empNameTrim,
      email: payload.email,
      role: payload.role,
      department: payload.department,
      status: "Active",
      phone: payload.phone,
      date_of_joining: payload.date_of_joining,
      has_photo: !!newEmpPhoto,
    };
    // Cache locally so the new employee shows up immediately even if the
    // subsequent list refresh is briefly stale.
    await saveLocalEmployee(newStaffObj);
    setEmployees((prev) => [newStaffObj, ...prev.filter((e) => (e.employee_id || e.id) !== empIdTrim)]);

    if (newEmpPhoto) {
      try {
        const formData = new FormData();
        formData.append("employee_id", empIdTrim);
        formData.append("photo", {
          uri: newEmpPhoto,
          name: `${empIdTrim}.jpg`,
          type: "image/jpeg",
        });
        await uploadEmployeePhoto(formData).catch(() => null);
      } catch (_) {}
    }

    await loadData();

    Alert.alert(
      "Staff Registered 🎉",
      `Employee '${empNameTrim}' (${empIdTrim}) registered successfully!\n\nDefault Password: ${empPassTrim}`
    );
    setAddModalVisible(false);
    setNewEmpId("");
    setNewEmpName("");
    setNewEmpRole("");
    setNewEmpDept("");
    setNewEmpEmail("");
    setNewEmpPhone("");
    setNewEmpDoj(new Date().toISOString().split("T")[0]);
    setNewEmpPassword("");
    setNewEmpPhoto(null);
    setSubmitting(false);
  };

  const openEditModal = (emp) => {
    setEditEmpId(emp.employee_id || emp.id);
    setEditEmpName(emp.name || "");
    setEditEmpEmail(emp.email || "");
    setEditEmpRole(emp.role || "");
    setEditEmpDoj(emp.date_of_joining || "");
    setSelectedEmp(null);
    setEditModalVisible(true);
  };

  const handleEditEmployeeSubmit = async () => {
    const nameTrim = editEmpName.trim();
    if (!nameTrim) {
      Alert.alert("Input Required", "Full Name is required.");
      return;
    }
    setEditSubmitting(true);
    const payload = {
      name: nameTrim,
      email: editEmpEmail.trim(),
      role: editEmpRole.trim(),
      date_of_joining: editEmpDoj.trim(),
    };

    let res;
    try {
      res = await editEmployee(editEmpId, payload);
    } catch (e) {
      res = e?.response;
    }

    if (!res?.data?.ok) {
      Alert.alert("Update Failed", res?.data?.msg || "Could not update this employee. Please try again.");
      setEditSubmitting(false);
      return;
    }

    setEmployees((prev) =>
      prev.map((e) =>
        (e.employee_id || e.id) === editEmpId ? { ...e, ...payload } : e
      )
    );
    await saveLocalEmployee({ employee_id: editEmpId, ...payload });

    setEditSubmitting(false);
    setEditModalVisible(false);
    Alert.alert("Updated", `${nameTrim}'s details were updated.`);
  };

  const handleDeleteEmployee = (emp) => {
    const empId = emp.employee_id || emp.id;
    Alert.alert(
      "Remove Staff Member",
      `Remove ${emp.name || empId}? This permanently deletes their attendance, salary, leave, resignation, and ticket history. This cannot be undone.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Remove",
          style: "destructive",
          onPress: async () => {
            setDeleting(true);
            let res;
            try {
              res = await deleteEmployee(empId);
            } catch (e) {
              res = e?.response;
            }
            setDeleting(false);
            if (!res?.data?.ok) {
              Alert.alert("Removal Failed", res?.data?.msg || "Could not remove this employee. Please try again.");
              return;
            }
            await deleteLocalEmployee(empId);
            setEmployees((prev) => prev.filter((e) => (e.employee_id || e.id) !== empId));
            setSelectedEmp(null);
            Alert.alert("Removed", `${emp.name || empId} has been removed.`);
          },
        },
      ]
    );
  };

  const departments = [
    "All",
    ...[...new Set(employees.map((emp) => emp.department).filter(Boolean))].sort(),
  ];
  const statuses = ["All", "Active", "On Leave", "Inactive"];
  const sortOptions = ["Name (A-Z)", "Name (Z-A)", "Role"];

  const hasActiveFilter = selectedDept !== "All" || selectedStatus !== "All" || selectedSort !== "Name (A-Z)";

  const filteredEmployees = employees
    .filter((emp) => {
      const empName = emp.name || emp.employee_id || "";
      const matchesSearch =
        !search ||
        empName.toLowerCase().includes(search.toLowerCase()) ||
        (emp.employee_id && emp.employee_id.toLowerCase().includes(search.toLowerCase())) ||
        (emp.role && emp.role.toLowerCase().includes(search.toLowerCase()));

      const empDept = emp.department || "";
      const matchesDept = selectedDept === "All" || empDept.toLowerCase() === selectedDept.toLowerCase();

      const empStatus = emp.status || "Active";
      const matchesStatus =
        selectedStatus === "All" ||
        empStatus.toLowerCase() === selectedStatus.toLowerCase() ||
        (selectedStatus === "On Leave" && (empStatus === "leave" || empStatus === "on leave"));

      return matchesSearch && matchesDept && matchesStatus;
    })
    .sort((a, b) => {
      if (selectedSort === "Name (Z-A)") return (b.name || "").localeCompare(a.name || "");
      if (selectedSort === "Role") return (a.role || "").localeCompare(b.role || "");
      return (a.name || "").localeCompare(b.name || "");
    });

  return (
    <LinearGradient colors={colors.screenGradient} style={styles.container}>
      <SafeAreaView style={{ flex: 1 }}>
        <AdminHeader
          title="Staff Directory"
          onMenu={() => navigation.dispatch(DrawerActions.openDrawer())}
        />

        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              colors={[colors.primary]}
            />
          }
        >
          {/* Summary Hero Card */}
          <View style={styles.heroCard}>
            <View style={styles.heroRow}>
              <View>
                <Text style={styles.heroNumber}>{employees.length}</Text>
                <Text style={styles.heroTitle}>Total Employees</Text>
              </View>
              <View style={styles.heroIconBadge}>
                <Ionicons name="people" size={28} color="#FFFFFF" />
              </View>
            </View>
            <Text style={styles.heroSubtitle}>
              {employees.filter((e) => e.status === "Active").length} Active •{" "}
              {employees.filter((e) => e.status === "On Leave" || e.status === "Leave").length} On Leave
            </Text>
          </View>

          {/* Seat-limit banner -- same data as web's employees.html banner */}
          {billingStatus && billingStatus.paid_employee_slots != null && (
            <TouchableOpacity
              style={[
                styles.seatBanner,
                billingStatus.employee_count >= billingStatus.paid_employee_slots && styles.seatBannerDanger,
              ]}
              onPress={() => navigation.navigate("SeatsBilling")}
              activeOpacity={0.85}
            >
              <Ionicons
                name={billingStatus.employee_count >= billingStatus.paid_employee_slots ? "lock-closed" : "people"}
                size={16}
                color={billingStatus.employee_count >= billingStatus.paid_employee_slots ? "#991B1B" : "#0A4E96"}
              />
              <Text
                style={[
                  styles.seatBannerText,
                  billingStatus.employee_count >= billingStatus.paid_employee_slots && { color: "#991B1B" },
                ]}
              >
                Seats: {billingStatus.employee_count} / {billingStatus.paid_employee_slots} used
                {billingStatus.employee_count >= billingStatus.paid_employee_slots ? " — tap to buy more" : ""}
              </Text>
              <Ionicons name="chevron-forward" size={16} color={colors.textLight} />
            </TouchableOpacity>
          )}

          {/* Search & Filter */}
          <AdminSearchBar
            value={search}
            onChangeText={setSearch}
            placeholder="Search by name, ID, or role..."
            onFilterPress={() => setFilterModalVisible(true)}
            hasActiveFilter={hasActiveFilter}
            onClear={() => setSearch("")}
          />

          {/* Department Filter Chips */}
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipScroll}>
            {departments.map((dept) => (
              <TouchableOpacity
                key={dept}
                style={[
                  styles.chip,
                  selectedDept === dept && styles.chipActive,
                ]}
                onPress={() => setSelectedDept(dept)}
              >
                <Text
                  style={[
                    styles.chipText,
                    selectedDept === dept && styles.chipTextActive,
                  ]}
                >
                  {dept}
                </Text>
              </TouchableOpacity>
            ))}
          </ScrollView>

          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Employee List</Text>
            <Text style={styles.sectionBadge}>{filteredEmployees.length} Results</Text>
          </View>

          {loading ? (
            <ActivityIndicator size="large" color="#0B6E8E" style={{ marginTop: 30 }} />
          ) : filteredEmployees.length === 0 ? (
            <View style={{ padding: 32, alignItems: "center", justifyContent: "center", backgroundColor: colors.card, borderRadius: 16, marginTop: 12, borderWidth: 1, borderColor: colors.border }}>
              <Ionicons name="people-outline" size={48} color={colors.textLight} />
              <Text style={{ fontSize: 16, fontWeight: "700", color: "#2E3D43", marginTop: 12 }}>
                No Staff Members Found
              </Text>
              <Text style={{ fontSize: 13, color: "#5A6A70", textAlign: "center", marginTop: 4, marginBottom: 16 }}>
                Your directory is currently empty. Tap below to register your first staff member.
              </Text>
              <TouchableOpacity
                style={{ backgroundColor: "#0B6E8E", paddingHorizontal: 20, paddingVertical: 11, borderRadius: 12 }}
                onPress={() => setAddModalVisible(true)}
              >
                <Text style={{ color: "#FFFFFF", fontWeight: "700", fontSize: 14 }}>+ Add First Staff Member</Text>
              </TouchableOpacity>
            </View>
          ) : (
            filteredEmployees.map((emp) => (
              <TouchableOpacity
                key={emp.id || emp.employee_id}
                style={styles.employeeCard}
                activeOpacity={0.8}
                onPress={() => setSelectedEmp(emp)}
              >
                {emp.photo || emp.has_photo ? (
                  <Image
                    source={{ uri: getPhotoUrl(emp.employee_id) }}
                    style={{ width: 48, height: 48, borderRadius: 24, borderWidth: 1.5, borderColor: "#CDEBF2" }}
                  />
                ) : (
                  <View style={styles.avatar}>
                    <Text style={styles.avatarText}>
                      {emp.name ? emp.name.charAt(0) : "E"}
                    </Text>
                  </View>
                )}

                <View style={styles.employeeInfo}>
                  <Text style={styles.employeeName}>{emp.name}</Text>
                  <Text style={styles.employeeId}>{emp.employee_id}</Text>
                  <Text style={styles.employeeRole}>
                    {emp.role} • {emp.department}
                  </Text>
                </View>

                <View style={styles.rightSection}>
                  <View
                    style={[
                      styles.statusBadge,
                      emp.status === "Active"
                        ? styles.statusActive
                        : emp.status === "Inactive"
                        ? styles.statusInactive
                        : styles.statusLeave,
                    ]}
                  >
                    <Text
                      style={[
                        styles.statusText,
                        emp.status === "Active"
                          ? styles.statusTextActive
                          : emp.status === "Inactive"
                          ? styles.statusTextInactive
                          : styles.statusTextLeave,
                      ]}
                    >
                      {emp.status}
                    </Text>
                  </View>
                  <Ionicons name="chevron-forward" size={18} color={colors.textLight} style={{ marginTop: 8 }} />
                </View>
              </TouchableOpacity>
            ))
          )}

          <View style={{ height: 110 }} />
        </ScrollView>

        {/* Comprehensive Employee Detail Modal */}
        <Modal visible={!!selectedEmp} transparent animationType="fade" onRequestClose={() => setSelectedEmp(null)}>
          <View style={styles.modalOverlay}>
            <View style={[styles.modalCard, { padding: 22 }]}>
              {selectedEmp && (
                <>
                  <View style={styles.modalHeader}>
                    <View style={[styles.modalAvatar, { backgroundColor: "#0B6E8E" }]}>
                      <Text style={[styles.modalAvatarText, { color: "#FFFFFF", fontWeight: "900" }]}>
                        {(selectedEmp.name || "E").charAt(0).toUpperCase()}
                      </Text>
                    </View>
                    <Text style={styles.modalName}>{selectedEmp.name}</Text>
                    <Text style={styles.modalRole}>
                      {selectedEmp.role || "Staff Member"} • {selectedEmp.department || "General"}
                    </Text>
                    <Text style={styles.modalEmpId}>ID: {selectedEmp.employee_id}</Text>
                  </View>

                  <View style={styles.modalDivider} />

                  <View style={{ gap: 10, marginVertical: 6 }}>
                    <View style={{ flexDirection: "row", alignItems: "center" }}>
                      <Ionicons name="mail-outline" size={16} color="#0B6E8E" />
                      <Text style={{ fontSize: 13, color: "#5A6A70", marginLeft: 8, fontWeight: "600" }}>Email:</Text>
                      <Text style={{ fontSize: 13, color: colors.text, marginLeft: 6, fontWeight: "700" }}>
                        {selectedEmp.email || "Not set"}
                      </Text>
                    </View>

                    <View style={{ flexDirection: "row", alignItems: "center" }}>
                      <Ionicons name="calendar-outline" size={16} color="#0B6E8E" />
                      <Text style={{ fontSize: 13, color: "#5A6A70", marginLeft: 8, fontWeight: "600" }}>Joined:</Text>
                      <Text style={{ fontSize: 13, color: colors.text, marginLeft: 6, fontWeight: "700" }}>
                        {selectedEmp.joining_date || selectedEmp.date_of_joining || "Recently"}
                      </Text>
                    </View>

                    <View style={{ flexDirection: "row", alignItems: "center" }}>
                      <Ionicons name="shield-checkmark-outline" size={16} color="#0B6E8E" />
                      <Text style={{ fontSize: 13, color: "#5A6A70", marginLeft: 8, fontWeight: "600" }}>Status:</Text>
                      <View style={{ backgroundColor: selectedEmp.status === "Active" ? "#DCFCE7" : "#FEF3C7", paddingHorizontal: 8, paddingVertical: 2, borderRadius: 10, marginLeft: 6 }}>
                        <Text style={{ fontSize: 12, fontWeight: "700", color: selectedEmp.status === "Active" ? "#16A34A" : "#D97706" }}>
                          {selectedEmp.status || "Active"}
                        </Text>
                      </View>
                    </View>
                  </View>

                  <View style={{ flexDirection: "row", gap: 10, marginTop: 18 }}>
                    <TouchableOpacity
                      style={{ flex: 1, backgroundColor: "#F0FDFA", borderWidth: 1, borderColor: "#99F6E4", borderRadius: 12, paddingVertical: 10, alignItems: "center" }}
                      onPress={() => {
                        const emp = selectedEmp;
                        setSelectedEmp(null);
                        navigation.navigate("IdCard", { empId: emp.employee_id, empName: emp.name });
                      }}
                    >
                      <Text style={{ color: "#0F766E", fontWeight: "700", fontSize: 13 }}>ID Card</Text>
                    </TouchableOpacity>

                    <TouchableOpacity
                      style={{ flex: 1, backgroundColor: colors.blueBg, borderWidth: 1, borderColor: "#B5DFE8", borderRadius: 12, paddingVertical: 10, alignItems: "center" }}
                      onPress={() => openEditModal(selectedEmp)}
                    >
                      <Text style={{ color: "#0A5878", fontWeight: "700", fontSize: 13 }}>Edit</Text>
                    </TouchableOpacity>

                    <TouchableOpacity
                      style={{ flex: 1, backgroundColor: colors.danger, borderRadius: 12, paddingVertical: 10, alignItems: "center" }}
                      onPress={() => handleDeleteEmployee(selectedEmp)}
                      disabled={deleting}
                    >
                      {deleting ? (
                        <ActivityIndicator size="small" color="#FFFFFF" />
                      ) : (
                        <Text style={{ color: "#FFFFFF", fontWeight: "700", fontSize: 13 }}>Remove</Text>
                      )}
                    </TouchableOpacity>

                    <TouchableOpacity
                      style={{ flex: 1, backgroundColor: "#0B6E8E", borderRadius: 12, paddingVertical: 10, alignItems: "center" }}
                      onPress={() => setSelectedEmp(null)}
                    >
                      <Text style={{ color: "#FFFFFF", fontWeight: "700", fontSize: 13 }}>Close</Text>
                    </TouchableOpacity>
                  </View>
                </>
              )}
            </View>
          </View>
        </Modal>

        {/* Edit Employee Modal -- fields mirror what
            blueprints/employees.py's api_edit_employee() actually accepts
            (name/email/role/date_of_joining only -- no department/phone,
            since the backend column set for PUT differs from POST). */}
        <Modal visible={editModalVisible} transparent animationType="slide" onRequestClose={() => setEditModalVisible(false)}>
          <View style={{ flex: 1, backgroundColor: "rgba(15, 23, 42, 0.8)", justifyContent: "flex-end" }}>
            <View style={{ backgroundColor: colors.card, borderTopLeftRadius: 28, borderTopRightRadius: 28, padding: 20, maxHeight: "90%" }}>
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 14, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: "#ECF3F5" }}>
                <View style={{ flexDirection: "row", alignItems: "center" }}>
                  <Ionicons name="create-outline" size={20} color="#0B6E8E" style={{ marginRight: 8 }} />
                  <Text style={{ fontSize: 18, fontWeight: "800", color: colors.text }}>Edit {editEmpId}</Text>
                </View>
                <TouchableOpacity onPress={() => setEditModalVisible(false)}>
                  <Ionicons name="close-circle" size={26} color="#5A6A70" />
                </TouchableOpacity>
              </View>

              <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 30 }}>
                <Text style={{ fontSize: 11, fontWeight: "700", color: "#5A6A70", marginTop: 4 }}>FULL NAME *</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginTop: 4 }}
                  value={editEmpName}
                  onChangeText={setEditEmpName}
                />

                <Text style={{ fontSize: 11, fontWeight: "700", color: "#5A6A70", marginTop: 10 }}>EMAIL ADDRESS</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginTop: 4 }}
                  value={editEmpEmail}
                  onChangeText={setEditEmpEmail}
                  keyboardType="email-address"
                  autoCapitalize="none"
                />

                <Text style={{ fontSize: 11, fontWeight: "700", color: "#5A6A70", marginTop: 10 }}>JOB ROLE / DESIGNATION</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginTop: 4 }}
                  value={editEmpRole}
                  onChangeText={setEditEmpRole}
                />

                <Text style={{ fontSize: 11, fontWeight: "700", color: "#5A6A70", marginTop: 10 }}>DATE OF JOINING</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginTop: 4 }}
                  placeholder="YYYY-MM-DD"
                  placeholderTextColor={colors.textLight}
                  value={editEmpDoj}
                  onChangeText={setEditEmpDoj}
                />

                <TouchableOpacity
                  style={{ backgroundColor: "#0B6E8E", borderRadius: 14, paddingVertical: 14, alignItems: "center", marginTop: 20, marginBottom: 10 }}
                  onPress={handleEditEmployeeSubmit}
                  disabled={editSubmitting}
                >
                  {editSubmitting ? (
                    <ActivityIndicator color="#FFFFFF" />
                  ) : (
                    <Text style={{ color: "#FFFFFF", fontWeight: "800", fontSize: 14 }}>Save Changes</Text>
                  )}
                </TouchableOpacity>
              </ScrollView>
            </View>
          </View>
        </Modal>

        {/* Floating Add Employee Button */}
        <TouchableOpacity
          activeOpacity={0.88}
          style={{
            position: "absolute",
            right: 20,
            bottom: 75,
            backgroundColor: "#0B6E8E",
            width: 56,
            height: 56,
            borderRadius: 28,
            justifyContent: "center",
            alignItems: "center",
            elevation: 8,
            shadowColor: "#0B6E8E",
            shadowOpacity: 0.4,
            shadowRadius: 10,
            shadowOffset: { width: 0, height: 4 },
          }}
          onPress={() => setAddModalVisible(true)}
        >
          <Ionicons name="person-add" size={24} color="#FFFFFF" />
        </TouchableOpacity>

        {/* Floating Email Blast Button */}
        <TouchableOpacity
          activeOpacity={0.88}
          style={{
            position: "absolute",
            right: 20,
            bottom: 141,
            backgroundColor: "#0EA5E9",
            width: 48,
            height: 48,
            borderRadius: 24,
            justifyContent: "center",
            alignItems: "center",
            elevation: 8,
            shadowColor: "#0EA5E9",
            shadowOpacity: 0.4,
            shadowRadius: 10,
            shadowOffset: { width: 0, height: 4 },
          }}
          onPress={() => setBlastModalVisible(true)}
        >
          <Ionicons name="mail" size={20} color="#FFFFFF" />
        </TouchableOpacity>

        {/* Add Employee Modal */}
        <Modal visible={addModalVisible} transparent animationType="slide" onRequestClose={() => setAddModalVisible(false)}>
          <View style={{ flex: 1, backgroundColor: "rgba(15, 23, 42, 0.8)", justifyContent: "flex-end" }}>
            <View style={{ backgroundColor: colors.card, borderTopLeftRadius: 28, borderTopRightRadius: 28, padding: 20, maxHeight: "90%" }}>
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 14, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: "#ECF3F5" }}>
                <View style={{ flexDirection: "row", alignItems: "center" }}>
                  <Ionicons name="person-add" size={20} color="#0B6E8E" style={{ marginRight: 8 }} />
                  <Text style={{ fontSize: 18, fontWeight: "800", color: colors.text }}>Add New Staff Member</Text>
                </View>
                <TouchableOpacity onPress={() => setAddModalVisible(false)}>
                  <Ionicons name="close-circle" size={26} color="#5A6A70" />
                </TouchableOpacity>
              </View>

              <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 30 }}>
                {/* 1-Tap Quick Fill Demo Preset -- dev convenience only */}
                {__DEV__ && (
                  <TouchableOpacity
                    style={{ flexDirection: "row", justifyContent: "center", alignItems: "center", backgroundColor: "#FEF3C7", borderWidth: 1, borderColor: "#FDE68A", paddingVertical: 10, borderRadius: 12, marginBottom: 14 }}
                    onPress={handleQuickFill}
                  >
                    <Ionicons name="flash" size={15} color="#D97706" style={{ marginRight: 6 }} />
                    <Text style={{ fontSize: 12, fontWeight: "800", color: "#B45309" }}>⚡ Quick Fill Demo Staff Preset (dev only)</Text>
                  </TouchableOpacity>
                )}

                {/* Face Photo Registration Box */}
                <View style={{ alignItems: "center", marginBottom: 16, backgroundColor: colors.background, padding: 16, borderRadius: 18, borderWidth: 2, borderColor: "#B5DFE8", borderStyle: "dashed" }}>
                  <Text style={{ fontSize: 11, fontWeight: "800", color: "#0C3054", letterSpacing: 0.8, marginBottom: 10, alignSelf: "flex-start" }}>
                    FACE PHOTO REGISTRATION *
                  </Text>
                  
                  {newEmpPhoto ? (
                    <View style={{ position: "relative" }}>
                      <Image source={{ uri: newEmpPhoto }} style={{ width: 84, height: 84, borderRadius: 42, borderWidth: 3, borderColor: colors.success }} />
                      <View style={{ position: "absolute", bottom: 0, right: 0, backgroundColor: colors.success, width: 22, height: 22, borderRadius: 11, justifyContent: "center", alignItems: "center", borderWidth: 2, borderColor: "#FFFFFF" }}>
                        <Ionicons name="checkmark" size={14} color="#FFFFFF" />
                      </View>
                    </View>
                  ) : (
                    <View style={{ width: 84, height: 84, borderRadius: 42, backgroundColor: colors.blueBg, borderWidth: 2, borderColor: "#CDEBF2", justifyContent: "center", alignItems: "center" }}>
                      <Ionicons name="camera" size={32} color="#0A5878" />
                    </View>
                  )}

                  <Text style={{ fontSize: 11, fontWeight: "600", color: "#5A6A70", marginTop: 8 }}>
                    {newEmpPhoto ? "Photo Attached Successfully" : "No face photo selected yet"}
                  </Text>

                  <View style={{ flexDirection: "row", gap: 12, marginTop: 12 }}>
                    <TouchableOpacity
                      style={{ flexDirection: "row", alignItems: "center", backgroundColor: "#0B6E8E", paddingHorizontal: 14, paddingVertical: 8, borderRadius: 12, elevation: 2 }}
                      onPress={handleTakePhoto}
                    >
                      <Ionicons name="camera-outline" size={16} color="#FFFFFF" style={{ marginRight: 6 }} />
                      <Text style={{ fontSize: 12, fontWeight: "800", color: "#FFFFFF" }}>📷 Camera</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={{ flexDirection: "row", alignItems: "center", backgroundColor: colors.blueBg, paddingHorizontal: 14, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: "#B5DFE8" }}
                      onPress={handlePickPhoto}
                    >
                      <Ionicons name="image-outline" size={16} color="#0A5878" style={{ marginRight: 6 }} />
                      <Text style={{ fontSize: 12, fontWeight: "800", color: "#0A5878" }}>🖼 Upload</Text>
                    </TouchableOpacity>
                  </View>
                </View>

                <Text style={{ fontSize: 11, fontWeight: "700", color: "#5A6A70", marginTop: 4 }}>FULL NAME *</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginTop: 4 }}
                  placeholder="Ravi Kumar"
                  value={newEmpName}
                  onChangeText={setNewEmpName}
                />

                <Text style={{ fontSize: 11, fontWeight: "700", color: "#5A6A70", marginTop: 10 }}>EMPLOYEE ID *</Text>
                <View style={{ flexDirection: "row", gap: 8, alignItems: "center", marginTop: 4 }}>
                  <TextInput
                    style={{ flex: 1, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, fontWeight: "800", letterSpacing: 0.5 }}
                    placeholder="EMP-1006"
                    value={newEmpId}
                    onChangeText={setNewEmpId}
                    autoCapitalize="characters"
                  />
                  <TouchableOpacity
                    style={{ backgroundColor: colors.blueBg, borderWidth: 1, borderColor: "#CDEBF2", paddingHorizontal: 12, paddingVertical: 11, borderRadius: 10 }}
                    onPress={handleAutoGenerateId}
                  >
                    <Text style={{ fontSize: 12, fontWeight: "700", color: "#0A5878" }}>🔄 Generate</Text>
                  </TouchableOpacity>
                </View>

                <Text style={{ fontSize: 11, fontWeight: "700", color: "#5A6A70", marginTop: 10 }}>EMAIL ADDRESS *</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginTop: 4 }}
                  placeholder="employee@company.com"
                  placeholderTextColor={colors.textLight}
                  value={newEmpEmail}
                  onChangeText={setNewEmpEmail}
                  keyboardType="email-address"
                  autoCapitalize="none"
                />

                <Text style={{ fontSize: 11, fontWeight: "700", color: "#5A6A70", marginTop: 10 }}>PHONE NUMBER</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginTop: 4 }}
                  placeholder="9876543210"
                  placeholderTextColor={colors.textLight}
                  value={newEmpPhone}
                  onChangeText={setNewEmpPhone}
                  keyboardType="phone-pad"
                />

                <Text style={{ fontSize: 11, fontWeight: "700", color: "#5A6A70", marginTop: 10 }}>DATE OF JOINING</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginTop: 4 }}
                  placeholder="YYYY-MM-DD"
                  value={newEmpDoj}
                  onChangeText={setNewEmpDoj}
                />

                <Text style={{ fontSize: 11, fontWeight: "700", color: "#5A6A70", marginTop: 10 }}>DEPARTMENT</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginTop: 4 }}
                  placeholder="Engineering"
                  placeholderTextColor={colors.textLight}
                  value={newEmpDept}
                  onChangeText={setNewEmpDept}
                />

                <Text style={{ fontSize: 11, fontWeight: "700", color: "#5A6A70", marginTop: 10 }}>JOB ROLE / DESIGNATION</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginTop: 4 }}
                  placeholder="Software Engineer"
                  placeholderTextColor={colors.textLight}
                  value={newEmpRole}
                  onChangeText={setNewEmpRole}
                />

                <Text style={{ fontSize: 11, fontWeight: "700", color: "#5A6A70", marginTop: 10 }}>INITIAL PASSWORD</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, marginTop: 4 }}
                  placeholder="At least 8 characters"
                  placeholderTextColor={colors.textLight}
                  value={newEmpPassword}
                  onChangeText={setNewEmpPassword}
                  secureTextEntry
                />

                <TouchableOpacity
                  style={{ backgroundColor: "#0B6E8E", borderRadius: 14, paddingVertical: 14, alignItems: "center", marginTop: 20, marginBottom: 10 }}
                  onPress={handleAddEmployeeSubmit}
                  disabled={submitting}
                >
                  {submitting ? (
                    <ActivityIndicator color="#FFFFFF" />
                  ) : (
                    <Text style={{ color: "#FFFFFF", fontWeight: "800", fontSize: 15 }}>Create Staff Profile</Text>
                  )}
                </TouchableOpacity>
              </ScrollView>
            </View>
          </View>
        </Modal>

        {/* Email Blast Compose Modal -- Bearer twin of blueprints/email_blast.py's
            api_email_blast, previously session-only and unreachable from mobile. */}
        <Modal visible={blastModalVisible} transparent animationType="slide" onRequestClose={() => setBlastModalVisible(false)}>
          <View style={{ flex: 1, backgroundColor: "rgba(15, 23, 42, 0.5)", justifyContent: "flex-end" }}>
            <View style={{ backgroundColor: colors.card, borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: 20, maxHeight: "85%" }}>
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
                <Text style={{ fontSize: 17, fontWeight: "800", color: colors.text }}>Broadcast Email</Text>
                <TouchableOpacity onPress={() => setBlastModalVisible(false)}>
                  <Ionicons name="close" size={22} color="#5A6A70" />
                </TouchableOpacity>
              </View>

              <ScrollView showsVerticalScrollIndicator={false}>
                <Text style={{ fontSize: 11, fontWeight: "800", color: "#5A6A70", letterSpacing: 0.6, marginBottom: 8 }}>SEND TO</Text>
                <View style={{ flexDirection: "row", gap: 8, marginBottom: 14 }}>
                  {[
                    { key: "all", label: "All Staff" },
                    { key: "department", label: "Department" },
                    { key: "individual", label: "One Employee" },
                  ].map((opt) => (
                    <TouchableOpacity
                      key={opt.key}
                      style={{
                        flex: 1, paddingVertical: 10, borderRadius: 10, alignItems: "center",
                        backgroundColor: blastTargetType === opt.key ? colors.primary : "#ECF3F5",
                        borderWidth: 1, borderColor: blastTargetType === opt.key ? colors.primary : colors.border,
                      }}
                      onPress={() => { setBlastTargetType(opt.key); setBlastTargetValue(""); }}
                    >
                      <Text style={{ fontSize: 11, fontWeight: "700", color: blastTargetType === opt.key ? "#FFFFFF" : colors.textSecondary }}>
                        {opt.label}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>

                {blastTargetType === "department" && (
                  <View style={{ marginBottom: 14 }}>
                    <Text style={{ fontSize: 11, fontWeight: "800", color: "#5A6A70", letterSpacing: 0.6, marginBottom: 8 }}>DEPARTMENT</Text>
                    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                      {[...new Set(employees.map((e) => e.department).filter(Boolean))].map((dept) => (
                        <TouchableOpacity
                          key={dept}
                          style={{
                            paddingHorizontal: 14, paddingVertical: 8, borderRadius: 10,
                            backgroundColor: blastTargetValue === dept ? colors.primary : "#ECF3F5",
                            borderWidth: 1, borderColor: blastTargetValue === dept ? colors.primary : colors.border,
                          }}
                          onPress={() => setBlastTargetValue(dept)}
                        >
                          <Text style={{ fontSize: 12, fontWeight: "700", color: blastTargetValue === dept ? "#FFFFFF" : colors.textSecondary }}>
                            {dept}
                          </Text>
                        </TouchableOpacity>
                      ))}
                    </View>
                  </View>
                )}

                {blastTargetType === "individual" && (
                  <View style={{ marginBottom: 14 }}>
                    <Text style={{ fontSize: 11, fontWeight: "800", color: "#5A6A70", letterSpacing: 0.6, marginBottom: 8 }}>EMPLOYEE ID</Text>
                    <TextInput
                      style={{ backgroundColor: colors.background, borderWidth: 1.5, borderColor: colors.border, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 10, fontSize: 13, fontWeight: "600", color: colors.text }}
                      placeholder="e.g. GZT001"
                      value={blastTargetValue}
                      onChangeText={setBlastTargetValue}
                      autoCapitalize="characters"
                    />
                  </View>
                )}

                <Text style={{ fontSize: 11, fontWeight: "800", color: "#5A6A70", letterSpacing: 0.6, marginBottom: 8 }}>SUBJECT</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1.5, borderColor: colors.border, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 10, fontSize: 13, fontWeight: "600", color: colors.text, marginBottom: 14 }}
                  placeholder="Announcement subject"
                  value={blastSubject}
                  onChangeText={setBlastSubject}
                />

                <Text style={{ fontSize: 11, fontWeight: "800", color: "#5A6A70", letterSpacing: 0.6, marginBottom: 8 }}>MESSAGE</Text>
                <TextInput
                  style={{ backgroundColor: colors.background, borderWidth: 1.5, borderColor: colors.border, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 10, fontSize: 13, fontWeight: "600", color: colors.text, minHeight: 100, textAlignVertical: "top", marginBottom: 18 }}
                  placeholder="Write your message..."
                  value={blastBody}
                  onChangeText={setBlastBody}
                  multiline
                />

                <TouchableOpacity
                  style={{ backgroundColor: colors.primary, flexDirection: "row", justifyContent: "center", alignItems: "center", paddingVertical: 14, borderRadius: 14 }}
                  onPress={handleSendBlast}
                  disabled={sendingBlast}
                >
                  {sendingBlast ? (
                    <ActivityIndicator color="#FFF" />
                  ) : (
                    <>
                      <Ionicons name="send" size={16} color="#FFF" />
                      <Text style={{ color: "#FFFFFF", fontSize: 13, fontWeight: "800", marginLeft: 8 }}>Send Broadcast</Text>
                    </>
                  )}
                </TouchableOpacity>
              </ScrollView>
            </View>
          </View>
        </Modal>

        {/* Professional SaaS Filter Modal */}
        <SaasFilterSheet
          visible={filterModalVisible}
          title="Filter Staff Directory"
          statusOptions={statuses}
          selectedStatus={selectedStatus}
          onSelectStatus={setSelectedStatus}
          deptOptions={departments}
          selectedDept={selectedDept}
          onSelectDept={setSelectedDept}
          sortOptions={sortOptions}
          selectedSort={selectedSort}
          onSelectSort={setSelectedSort}
          onApply={() => setFilterModalVisible(false)}
          onReset={() => {
            setSelectedDept("All");
            setSelectedStatus("All");
            setSelectedSort("Name (A-Z)");
          }}
          onClose={() => setFilterModalVisible(false)}
        />
      </SafeAreaView>
    </LinearGradient>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: { flex: 1 },
  content: { paddingHorizontal: 20, paddingTop: 10 },
  heroCard: {
    backgroundColor: "#0B6E8E",
    borderRadius: 24,
    padding: 20,
    marginBottom: 16,
    elevation: 4,
  },
  heroRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  heroNumber: { fontSize: 22, fontWeight: "800", color: "#FFFFFF" },
  heroTitle: { fontSize: 14, fontWeight: "700", color: "rgba(255,255,255,0.85)", marginTop: 2 },
  heroSubtitle: { fontSize: 12, color: "rgba(255,255,255,0.7)", marginTop: 8 },
  seatBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: colors.blueBg,
    borderWidth: 1,
    borderColor: "#B5DFE8",
    borderRadius: 12,
    paddingVertical: 10,
    paddingHorizontal: 12,
    marginTop: 12,
  },
  seatBannerDanger: { backgroundColor: "#FEE2E2", borderColor: "#FCA5A5" },
  seatBannerText: { flex: 1, fontSize: 12, fontWeight: "700", color: "#0A4E96" },
  heroIconBadge: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: "rgba(255,255,255,0.15)",
    justifyContent: "center",
    alignItems: "center",
  },
  chipScroll: { marginVertical: 12 },
  chip: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: colors.card,
    marginRight: 8,
    borderWidth: 1,
    borderColor: colors.border,
  },
  chipActive: { backgroundColor: "#0B6E8E", borderColor: "#0B6E8E" },
  chipText: { fontSize: 12, fontWeight: "600", color: "#5A6A70" },
  chipTextActive: { color: "#FFFFFF", fontWeight: "700" },
  sectionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginVertical: 12 },
  sectionTitle: { fontSize: 14, fontWeight: "800", color: colors.text },
  sectionBadge: { fontSize: 12, fontWeight: "700", color: "#0B6E8E" },
  employeeCard: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.card,
    borderRadius: 20,
    padding: 16,
    marginBottom: 10,
    elevation: 2,
    borderWidth: 1,
    borderColor: colors.border,
  },
  avatar: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: colors.primaryLight,
    justifyContent: "center",
    alignItems: "center",
  },
  avatarText: { fontSize: 15, fontWeight: "800", color: "#0B6E8E" },
  employeeInfo: { flex: 1, marginLeft: 14 },
  employeeName: { fontSize: 13, fontWeight: "700", color: colors.text },
  employeeId: { fontSize: 11, color: colors.textLight, marginTop: 2 },
  employeeRole: { fontSize: 12, color: "#5A6A70", marginTop: 2 },
  rightSection: { alignItems: "flex-end" },
  statusBadge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  statusActive: { backgroundColor: "#DCFCE7" },
  statusInactive: { backgroundColor: "#FEE2E2" },
  statusLeave: { backgroundColor: "#FEF3C7" },
  statusText: { fontSize: 11, fontWeight: "700" },
  statusTextActive: { color: "#166534" },
  statusTextInactive: { color: "#991B1B" },
  statusTextLeave: { color: "#B45309" },
  modalOverlay: { flex: 1, backgroundColor: "rgba(15,23,42,0.5)", justifyContent: "center", alignItems: "center", padding: 20 },
  modalCard: { width: "100%", backgroundColor: colors.card, borderRadius: 24, padding: 24, alignItems: "center", elevation: 10 },
  modalHeader: { alignItems: "center" },
  modalAvatar: { width: 64, height: 64, borderRadius: 32, backgroundColor: colors.primaryLight, justifyContent: "center", alignItems: "center", marginBottom: 12 },
  modalAvatarText: { fontSize: 24, fontWeight: "800", color: "#0B6E8E" },
  modalName: { fontSize: 20, fontWeight: "800", color: colors.text },
  modalRole: { fontSize: 14, color: "#5A6A70", marginTop: 4 },
  modalEmpId: { fontSize: 12, color: colors.textLight, marginTop: 2 },
  modalDivider: { width: "100%", height: 1, backgroundColor: "#ECF3F5", marginVertical: 16 },
  modalRow: { flexDirection: "row", alignItems: "center", marginBottom: 16 },
  modalLabel: { fontSize: 14, fontWeight: "600", color: "#5A6A70", marginLeft: 8 },
  modalValue: { fontSize: 14, fontWeight: "700", color: colors.text, marginLeft: 6 },
  closeBtn: { width: "100%", backgroundColor: "#0B6E8E", paddingVertical: 14, borderRadius: 16, alignItems: "center", marginTop: 10 },
  closeBtnText: { color: "#FFFFFF", fontSize: 15, fontWeight: "700" },
});