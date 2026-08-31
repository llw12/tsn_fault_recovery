package stream_aware_micro;

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

import tsn_fault_recovery.control.StreamForwardingRecorder;

network ScenarioNetwork extends Module
{
    parameters:
        bool hasGlobalArp = default(true);
        *.ipv4.arp.typename = default(hasGlobalArp ? "GlobalArp" : "Arp");
    submodules:
        destination: TsnDevice {
            @display("p=100,420");
        }
        source1: TsnDevice {
            @display("p=500,420");
        }
        source2: TsnDevice {
            @display("p=900,420");
        }
        s0: TsnSwitch {
            @display("p=100,240");
        }
        sA: TsnSwitch {
            @display("p=366,240");
        }
        sB: TsnSwitch {
            @display("p=632,240");
        }
        sD: TsnSwitch {
            @display("p=898,240");
        }
        visualizer: <default(firstAvailableOrEmpty("IntegratedCanvasVisualizer"))> like IIntegratedVisualizer if typename != "" { @display("p=80,70;is=s"); }
        configurator: <default("Ipv4NetworkConfigurator")> like IL3NetworkConfigurator if typename != "" { @display("p=170,70;is=s"); }
        macForwardingTableConfigurator: <default(hasGlobalArp ? "MacForwardingTableConfigurator" : "")> like INetworkConfigurator if typename != "" { @display("p=260,70;is=s"); }
        scenarioManager: ScenarioManager { @display("p=350,70;is=s"); }
        profileSwitcher: ProfileSwitcher { @display("p=440,70;is=s"); }
        scenarioRecoveryController: ScenarioRecoveryController { @display("p=530,70;is=s"); }
        packetIdentityRecorder: PacketIdentityRecorder { @display("p=620,70;is=s"); }
        streamForwardingRecorder: StreamForwardingRecorder { @display("p=710,70;is=s"); }
    connections:
        sA.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sD.ethg++; // l_a_d
        sB.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sD.ethg++; // l_b_d
        sD.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> destination.ethg++; // l_d_dst
        s0.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sA.ethg++; // l_s0_a
        s0.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sB.ethg++; // l_s0_b
        source1.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> s0.ethg++; // l_src1_s0
        source2.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> s0.ethg++; // l_src2_s0
}
