package structured30_auto;

import inet.common.Module;
import inet.common.scenario.ScenarioManager;
import inet.networklayer.configurator.contract.IL3NetworkConfigurator;
import inet.networklayer.configurator.contract.INetworkConfigurator;
import inet.node.ethernet.EthernetLink;
import inet.node.tsn.TsnDevice;
import inet.node.tsn.TsnSwitch;
import inet.visualizer.contract.IIntegratedVisualizer;
import tsn_fault_recovery.control.PacketIdentityRecorder;
import tsn_fault_recovery.control.ProfileSwitcher;
import tsn_fault_recovery.control.ScenarioRecoveryController;

network ScenarioNetwork extends Module
{
    parameters:
        bool hasGlobalArp = default(true);
        *.ipv4.arp.typename = default(hasGlobalArp ? "GlobalArp" : "Arp");
    submodules:
        es01: TsnDevice {
            @display("p=100,420");
        }
        es02: TsnDevice {
            @display("p=157,420");
        }
        es03: TsnDevice {
            @display("p=214,420");
        }
        es04: TsnDevice {
            @display("p=271,420");
        }
        es05: TsnDevice {
            @display("p=328,420");
        }
        es06: TsnDevice {
            @display("p=385,420");
        }
        es07: TsnDevice {
            @display("p=442,420");
        }
        es08: TsnDevice {
            @display("p=499,420");
        }
        es09: TsnDevice {
            @display("p=556,420");
        }
        es10: TsnDevice {
            @display("p=613,420");
        }
        es11: TsnDevice {
            @display("p=670,420");
        }
        es12: TsnDevice {
            @display("p=727,420");
        }
        es13: TsnDevice {
            @display("p=784,420");
        }
        es14: TsnDevice {
            @display("p=841,420");
        }
        es15: TsnDevice {
            @display("p=898,420");
        }
        sw01: TsnSwitch {
            @display("p=100,240");
        }
        sw02: TsnSwitch {
            @display("p=127,240");
        }
        sw03: TsnSwitch {
            @display("p=154,240");
        }
        sw04: TsnSwitch {
            @display("p=181,240");
        }
        sw05: TsnSwitch {
            @display("p=208,240");
        }
        sw06: TsnSwitch {
            @display("p=235,240");
        }
        sw07: TsnSwitch {
            @display("p=262,240");
        }
        sw08: TsnSwitch {
            @display("p=289,240");
        }
        sw09: TsnSwitch {
            @display("p=316,240");
        }
        sw10: TsnSwitch {
            @display("p=343,240");
        }
        sw11: TsnSwitch {
            @display("p=370,240");
        }
        sw12: TsnSwitch {
            @display("p=397,240");
        }
        sw13: TsnSwitch {
            @display("p=424,240");
        }
        sw14: TsnSwitch {
            @display("p=451,240");
        }
        sw15: TsnSwitch {
            @display("p=478,240");
        }
        sw16: TsnSwitch {
            @display("p=505,240");
        }
        sw17: TsnSwitch {
            @display("p=532,240");
        }
        sw18: TsnSwitch {
            @display("p=559,240");
        }
        sw19: TsnSwitch {
            @display("p=586,240");
        }
        sw20: TsnSwitch {
            @display("p=613,240");
        }
        sw21: TsnSwitch {
            @display("p=640,240");
        }
        sw22: TsnSwitch {
            @display("p=667,240");
        }
        sw23: TsnSwitch {
            @display("p=694,240");
        }
        sw24: TsnSwitch {
            @display("p=721,240");
        }
        sw25: TsnSwitch {
            @display("p=748,240");
        }
        sw26: TsnSwitch {
            @display("p=775,240");
        }
        sw27: TsnSwitch {
            @display("p=802,240");
        }
        sw28: TsnSwitch {
            @display("p=829,240");
        }
        sw29: TsnSwitch {
            @display("p=856,240");
        }
        sw30: TsnSwitch {
            @display("p=883,240");
        }
        visualizer: <default(firstAvailableOrEmpty("IntegratedCanvasVisualizer"))> like IIntegratedVisualizer if typename != "" { @display("p=80,70;is=s"); }
        configurator: <default("Ipv4NetworkConfigurator")> like IL3NetworkConfigurator if typename != "" { @display("p=170,70;is=s"); }
        macForwardingTableConfigurator: <default(hasGlobalArp ? "MacForwardingTableConfigurator" : "")> like INetworkConfigurator if typename != "" { @display("p=260,70;is=s"); }
        scenarioManager: ScenarioManager { @display("p=350,70;is=s"); }
        profileSwitcher: ProfileSwitcher { @display("p=440,70;is=s"); }
        scenarioRecoveryController: ScenarioRecoveryController { @display("p=530,70;is=s"); }
        packetIdentityRecorder: PacketIdentityRecorder { @display("p=620,70;is=s"); }
    connections:
        es01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw01.ethg++; // l_es01_sw01
        es02.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw03.ethg++; // l_es02_sw03
        es03.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw05.ethg++; // l_es03_sw05
        es04.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw07.ethg++; // l_es04_sw07
        es05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw09.ethg++; // l_es05_sw09
        es06.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw11.ethg++; // l_es06_sw11
        es07.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw13.ethg++; // l_es07_sw13
        es08.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw15.ethg++; // l_es08_sw15
        es09.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw17.ethg++; // l_es09_sw17
        es10.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw19.ethg++; // l_es10_sw19
        es11.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw21.ethg++; // l_es11_sw21
        es12.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw23.ethg++; // l_es12_sw23
        es13.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw25.ethg++; // l_es13_sw25
        es14.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw27.ethg++; // l_es14_sw27
        es15.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw29.ethg++; // l_es15_sw29
        sw01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw02.ethg++; // l_sw01_sw02
        sw01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw07.ethg++; // l_sw01_sw07
        sw01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw09.ethg++; // l_sw01_sw09
        sw02.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw03.ethg++; // l_sw02_sw03
        sw02.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw08.ethg++; // l_sw02_sw08
        sw03.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw04.ethg++; // l_sw03_sw04
        sw03.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw09.ethg++; // l_sw03_sw09
        sw04.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw05.ethg++; // l_sw04_sw05
        sw04.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw10.ethg++; // l_sw04_sw10
        sw05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw06.ethg++; // l_sw05_sw06
        sw05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw07.ethg++; // l_sw05_sw07
        sw05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw11.ethg++; // l_sw05_sw11
        sw06.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw12.ethg++; // l_sw06_sw12
        sw07.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw08.ethg++; // l_sw07_sw08
        sw07.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw13.ethg++; // l_sw07_sw13
        sw08.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw09.ethg++; // l_sw08_sw09
        sw08.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw14.ethg++; // l_sw08_sw14
        sw08.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw16.ethg++; // l_sw08_sw16
        sw09.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw10.ethg++; // l_sw09_sw10
        sw09.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw15.ethg++; // l_sw09_sw15
        sw10.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw11.ethg++; // l_sw10_sw11
        sw10.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw16.ethg++; // l_sw10_sw16
        sw11.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw12.ethg++; // l_sw11_sw12
        sw11.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw17.ethg++; // l_sw11_sw17
        sw12.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw14.ethg++; // l_sw12_sw14
        sw12.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw18.ethg++; // l_sw12_sw18
        sw13.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw14.ethg++; // l_sw13_sw14
        sw13.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw19.ethg++; // l_sw13_sw19
        sw14.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw15.ethg++; // l_sw14_sw15
        sw14.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw20.ethg++; // l_sw14_sw20
        sw15.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw16.ethg++; // l_sw15_sw16
        sw15.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw21.ethg++; // l_sw15_sw21
        sw15.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw23.ethg++; // l_sw15_sw23
        sw16.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw17.ethg++; // l_sw16_sw17
        sw16.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw22.ethg++; // l_sw16_sw22
        sw17.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw18.ethg++; // l_sw17_sw18
        sw17.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw23.ethg++; // l_sw17_sw23
        sw18.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw24.ethg++; // l_sw18_sw24
        sw19.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw20.ethg++; // l_sw19_sw20
        sw19.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw25.ethg++; // l_sw19_sw25
        sw20.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw21.ethg++; // l_sw20_sw21
        sw20.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw26.ethg++; // l_sw20_sw26
        sw21.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw22.ethg++; // l_sw21_sw22
        sw21.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw27.ethg++; // l_sw21_sw27
        sw22.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw23.ethg++; // l_sw22_sw23
        sw22.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw28.ethg++; // l_sw22_sw28
        sw22.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw30.ethg++; // l_sw22_sw30
        sw23.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw24.ethg++; // l_sw23_sw24
        sw23.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw29.ethg++; // l_sw23_sw29
        sw24.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw30.ethg++; // l_sw24_sw30
        sw25.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw26.ethg++; // l_sw25_sw26
        sw26.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw27.ethg++; // l_sw26_sw27
        sw27.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw28.ethg++; // l_sw27_sw28
        sw28.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw29.ethg++; // l_sw28_sw29
        sw29.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw30.ethg++; // l_sw29_sw30
}
