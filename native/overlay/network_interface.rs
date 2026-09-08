//! Opt-in macOS interface binding for the isolated Android host only.
#[cfg(target_os = "macos")]
pub(crate) fn bind(fd: std::os::fd::RawFd, ipv4: bool) -> std::io::Result<()> {
    use std::{ffi::CString, io};
    if std::env::var_os("RUSTDESK_ANDROID_BACKEND_CONFIG").is_none() {
        return Ok(());
    }
    let Some(name) = std::env::var_os("RUSTDESK_ANDROID_NETWORK_INTERFACE") else {
        return Ok(());
    };
    let name = CString::new(name.as_encoded_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid network interface"))?;
    // if_nametoindex reads a NUL-terminated name; setsockopt copies the index.
    let index = unsafe { libc::if_nametoindex(name.as_ptr()) };
    if index == 0 {
        return Err(io::Error::new(io::ErrorKind::NotFound, "network interface not found"));
    }
    let (level, option) = if ipv4 { (libc::IPPROTO_IP, libc::IP_BOUND_IF) }
        else { (libc::IPPROTO_IPV6, libc::IPV6_BOUND_IF) };
    let result = unsafe {
        libc::setsockopt(fd, level, option, &index as *const _ as *const libc::c_void,
            std::mem::size_of_val(&index) as libc::socklen_t)
    };
    if result != 0 { return Err(io::Error::last_os_error()); }
    Ok(())
}
