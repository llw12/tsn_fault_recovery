package mesh10;

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
        es1: TsnDevice {
            @display("p=100,420");
        }
        es2: TsnDevice {
            @display("p=260,420");
        }
        es3: TsnDevice {
            @display("p=420,420");
        }
        es4: TsnDevice {
            @display("p=580,420");
        }
        es5: TsnDevice {
            @display("p=740,420");
        }
        es6: TsnDevice {
            @display("p=900,420");
        }
        sw1: TsnSwitch {
            @display("p=100,240");
        }
        sw10: TsnSwitch {
            @display("p=188,240");
        }
        sw2: TsnSwitch {
            @display("p=276,240");
        }
        sw3: TsnSwitch {
            @display("p=364,240");
        }
        sw4: TsnSwitch {
            @display("p=452,240");
        }
        sw5: TsnSwitch {
            @display("p=540,240");
        }
        sw6: TsnSwitch {
            @display("p=628,240");
        }
        sw7: TsnSwitch {
            @display("p=716,240");
        }
        sw8: TsnSwitch {
            @display("p=804,240");
        }
        sw9: TsnSwitch {
            @display("p=892,240");
        }
        visualizer: <default(firstAvailableOrEmpty("IntegratedCanvasVisualizer"))> like IIntegratedVisualizer if typename != "" { @display("p=80,70;is=s"); }
        configurator: <default("Ipv4NetworkConfigurator")> like IL3NetworkConfigurator if typename != "" { @display("p=170,70;is=s"); }
        macForwardingTableConfigurator: <default(hasGlobalArp ? "MacForwardingTableConfigurator" : "")> like INetworkConfigurator if typename != "" { @display("p=260,70;is=s"); }
        scenarioManager: ScenarioManager { @display("p=350,70;is=s"); }
        profileSwitcher: ProfileSwitcher { @display("p=440,70;is=s"); }
        scenarioRecoveryController: ScenarioRecoveryController { @display("p=530,70;is=s"); }
        packetIdentityRecorder: PacketIdentityRecorder { @display("p=620,70;is=s"); }
    connections:
        es1.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw1.ethg++; // l_es1_sw1
        es2.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw2.ethg++; // l_es2_sw2
        es3.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw4.ethg++; // l_es3_sw4
        es4.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw7.ethg++; // l_es4_sw7
        es5.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw9.ethg++; // l_es5_sw9
        es6.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw10.ethg++; // l_es6_sw10
        sw1.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw2.ethg++; // l_sw1_sw2
        sw1.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw3.ethg++; // l_sw1_sw3
        sw2.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw3.ethg++; // l_sw2_sw3
        sw2.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw5.ethg++; // l_sw2_sw5
        sw3.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw4.ethg++; // l_sw3_sw4
        sw3.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw6.ethg++; // l_sw3_sw6
        sw4.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw6.ethg++; // l_sw4_sw6
        sw4.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw7.ethg++; // l_sw4_sw7
        sw5.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw6.ethg++; // l_sw5_sw6
        sw5.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw8.ethg++; // l_sw5_sw8
        sw6.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw7.ethg++; // l_sw6_sw7
        sw6.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw8.ethg++; // l_sw6_sw8
        sw7.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw9.ethg++; // l_sw7_sw9
        sw8.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw10.ethg++; // l_sw8_sw10
        sw8.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw9.ethg++; // l_sw8_sw9
        sw9.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw10.ethg++; // l_sw9_sw10
}
