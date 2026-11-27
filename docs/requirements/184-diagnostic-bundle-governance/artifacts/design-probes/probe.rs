use std::path::Path;
use std::io::{Read, Write};
#[path="baseline.rs"] mod baseline;
#[path="buffered.rs"] mod buffered;
fn nonce() -> u128 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos() }
fn redact_text(text: &str, credentials: &[String], home: Option<&Path>) -> (String, u64) {
    let mut result = text.to_string();
    let mut count = 0;
    for credential in credentials {
        let occurrences = result.matches(credential).count() as u64;
        if occurrences > 0 {
            result = result.replace(credential, "<REDACTED:CREDENTIAL>");
            count += occurrences;
        }
    }
    if let Some(home) = home.and_then(Path::to_str) {
        result = result.replace(home, "<USER_HOME>");
        #[cfg(target_os = "windows")]
        {
            result = result.replace(&home.replace('\\', "/"), "<USER_HOME>");
        }
    }
    let mut lines = Vec::new();
    for line in result.lines() {
        let lower = line.to_ascii_lowercase();
        if [
            "authorization:",
            "cookie:",
            "api_key=",
            "apikey=",
            "token=",
            "password=",
            "access_token=",
            "accesstoken=",
            "refresh_token=",
            "refreshtoken=",
            "client_secret=",
            "clientsecret=",
            "secret_key=",
            "secretkey=",
        ]
        .iter()
        .any(|marker| lower.contains(marker))
        {
            lines.push("<REDACTED:CREDENTIAL-LINE>".to_string());
            count += 1;
        } else {
            lines.push(line.to_string());
        }
    }
    (lines.join("\n"), count)
}

fn main() {
 let args:Vec<String>=std::env::args().collect();
 let path=Path::new(&args[2]);
 let creds=vec!["synthetic-secret".to_string(),"synthetic-unused-key".to_string()];
 let start=std::time::Instant::now();
 let (mut f, input, output, _owner): (std::fs::File,u64,usize,Box<dyn std::any::Any>) = if args[1]=="baseline" {
   let s=baseline::StagedLog::collect(path,&creds,Some(Path::new("C:/synthetic-home"))).unwrap();
   (s.file.try_clone().unwrap(),s.original_bytes,s.included_bytes,Box::new(s))
 } else {
   let s=buffered::StagedLog::collect(path,&creds,Some(Path::new("C:/synthetic-home"))).unwrap();
   (s.file.try_clone().unwrap(),s.original_bytes,s.included_bytes,Box::new(s))
 };
 let duration=start.elapsed().as_secs_f64();
 let mut hash:u64=14695981039346656037; let mut buf=[0u8;65536]; let mut n:u64=0;
 loop {let c=f.read(&mut buf).unwrap();if c==0 {break;}for b in &buf[..c] {hash^=*b as u64;hash=hash.wrapping_mul(1099511628211);}n+=c as u64;}
 assert_eq!(n,output as u64);
 println!("{} {} {} {:.6} {}",args[1],input,output,duration,hash);
 std::io::stdout().flush().unwrap();
}
