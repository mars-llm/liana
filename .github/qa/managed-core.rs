use super::*;
use crate::installer::step::install_bitcoind;
use lianad::config::BitcoindRpcAuth;
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::process::Command;

struct ManagedNode {
    node: Option<Bitcoind>,
    addr: SocketAddr,
    debug_log: PathBuf,
}

impl ManagedNode {
    fn stop(&mut self) {
        let Some(node) = self.node.take() else {
            return;
        };
        node.stop();
        for _ in 0..300 {
            if TcpStream::connect(self.addr).is_err()
                && std::fs::read_to_string(&self.debug_log).is_ok_and(|log| {
                    log.contains("Shutdown done") || log.contains("Shutdown: done")
                })
            {
                return;
            }
            thread::sleep(time::Duration::from_millis(100));
        }
        assert!(
            thread::panicking(),
            "Managed Bitcoin Core did not shut down"
        );
    }
}

impl Drop for ManagedNode {
    fn drop(&mut self) {
        self.stop();
    }
}

#[test]
fn qa_upgrades_a_real_managed_regtest_node() {
    let archive = std::env::var_os("LIANA_QA_CORE_ARCHIVE").unwrap();
    let bytes = std::fs::read(archive).unwrap();
    let directory = test_directory(&[]);
    let destination = internal_bitcoind_directory(&directory);
    std::fs::create_dir_all(&destination).unwrap();
    assert!(install_bitcoind(&destination, b"wrong archive").is_err());
    assert!(!internal_bitcoind_exe_path(&directory, VERSION).exists());
    assert_eq!(VERSION, "32.1");

    let previous = internal_bitcoind_exe_path(&directory, "31.1");
    std::fs::create_dir_all(previous.parent().unwrap()).unwrap();
    std::fs::copy(
        std::env::var_os("LIANA_QA_PREVIOUS_CORE").unwrap(),
        previous,
    )
    .unwrap();

    let datadir = internal_bitcoind_datadir(&directory);
    std::fs::create_dir_all(&datadir).unwrap();
    let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let addr = listener.local_addr().unwrap();
    let conf = format!(
        "listen=0\nlistenonion=0\nonion=0\ndnsseed=0\nfixedseeds=0\n+         [regtest]\nrpcbind=127.0.0.1\nrpcport={}\n",
        addr.port()
    );
    std::fs::write(internal_bitcoind_config_path(&datadir), conf).unwrap();
    let config = BitcoindConfig {
        rpc_auth: BitcoindRpcAuth::CookieFile(internal_bitcoind_cookie_path(
            &datadir,
            &Network::Regtest,
        )),
        addr,
    };
    drop(listener);

    let cli = std::env::var_os("LIANA_QA_BITCOIN_CLI").unwrap();
    let call = |args: &[&str]| {
        let response = Command::new(&cli)
            .arg("-regtest")
            .arg(format!("-datadir={}", datadir.display()))
            .arg(format!("-rpcport={}", addr.port()))
            .args(args)
            .output()
            .unwrap();
        assert!(response.status.success(), "{:?}", response.stderr);
        serde_json::from_slice::<serde_json::Value>(&response.stdout).unwrap()
    };
    let debug_log = internal_bitcoind_debug_log_path(&directory, Network::Regtest);
    let start = || ManagedNode {
        node: Some(Bitcoind::maybe_start(Network::Regtest, config.clone(), &directory).unwrap()),
        addr,
        debug_log: debug_log.clone(),
    };
    let mut node = start();
    assert_eq!(call(&["getnetworkinfo"])["version"], 310100);
    let address = bitcoin::Address::p2wsh(
        &bitcoin::ScriptBuf::from_bytes(vec![0x51]),
        Network::Regtest,
    )
    .to_string();
    let mined = call(&["generatetoaddress", "1", &address]);
    assert_eq!(mined.as_array().unwrap().len(), 1);
    node.stop();
    std::fs::rename(&debug_log, debug_log.with_extension("31.1.log")).unwrap();

    install_bitcoind(&destination, &bytes).unwrap();
    assert!(internal_bitcoind_exe_path(&directory, VERSION).is_file());
    let mut node = start();
    assert_eq!(call(&["getnetworkinfo"])["version"], 320100);
    let rpc = lianad::BitcoinD::new(&config, "managed_core_qa".to_string()).unwrap();
    assert_eq!(rpc.chain_tip().height, 1);
    assert_eq!(call(&["getblockchaininfo"])["bestblockhash"], mined[0]);
    node.stop();
}
